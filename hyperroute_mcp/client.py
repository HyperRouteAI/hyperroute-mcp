"""Thin async HTTP client over the HyperRoute router's public API.

One method per endpoint this server touches. No product logic lives here — the client only
knows how to (a) carry the session bearer token, and (b) turn the router's responses into
plain dicts, surfacing its structured `detail` error bodies instead of raising, so the MCP
tools can hand a coordinator an actionable object (e.g. `needs_onboard` + signup instructions)
rather than a stack trace.

One endpoint answers in text rather than JSON: `recommend` with `format=text` returns the
compact tabular coordinator wire, so `_request(..., as_text=True)` returns a `str`.
"""

from __future__ import annotations

from typing import Any

import httpx


class Session:
    """The MCP process's login state: the active bearer token and the resolved user_id.

    Mutated in place by `register` / `login` so every later recommend/onboard/execute call
    is authenticated as the same user for the life of the MCP connection.
    """

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key
        self.user_id: str | None = None
        self.email: str | None = None
        self.env_managed = False   # token came from HYPERROUTE_API_KEY — don't read/write the cache

    @property
    def logged_in(self) -> bool:
        return bool(self.api_key)


def _unwrap(r: httpx.Response) -> Any:
    """Router response -> dict. Success bodies pass through; error bodies are normalized to
    `{"_error": True, "_http_status": <code>, ...}` with the server's `detail` merged in."""
    try:
        body = r.json()
    except ValueError:
        body = {"raw": r.text}
    if r.is_success:
        return body
    detail = body.get("detail") if isinstance(body, dict) else None
    base = {"_error": True, "_http_status": r.status_code}
    if isinstance(detail, dict):
        return {**base, **detail}
    if detail is not None:
        return {**base, "message": detail}
    return {**base, "body": body}


class HyperRouteClient:
    """Stateless-per-call HTTP client bound to a mutable `Session` for auth."""

    def __init__(self, base_url: str, session: Session, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session
        self._timeout = timeout

    def _headers(self, auth: bool) -> dict[str, str]:
        h = {"content-type": "application/json"}
        if auth and self.session.api_key:
            h["authorization"] = f"Bearer {self.session.api_key}"
        return h

    async def _request(self, method: str, path: str, *, auth: bool = False,
                       json: dict | None = None, params: dict | None = None,
                       as_text: bool = False) -> Any:
        if json is not None:  # drop None fields so router defaults apply
            json = {k: v for k, v in json.items() if v is not None}
        if params is not None:
            params = {k: v for k, v in params.items() if v is not None}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                r = await c.request(method, f"{self.base_url}{path}",
                                    headers=self._headers(auth), json=json, params=params)
        except httpx.RequestError as e:
            return {"_error": True, "message": f"could not reach router at {self.base_url}: {e}"}
        if as_text and r.is_success:
            return r.text
        return _unwrap(r)

    # -- surfaces -----------------------------------------------------------
    async def health(self) -> Any:
        return await self._request("GET", "/health")

    async def register(self, email: str, password: str, display_name: str | None) -> Any:
        return await self._request("POST", "/auth/register",
                                   json={"email": email, "password": password,
                                         "display_name": display_name})

    async def verify(self, email: str, code: str) -> Any:
        return await self._request("POST", "/auth/verify", json={"email": email, "code": code})

    async def login(self, email: str, password: str) -> Any:
        return await self._request("POST", "/auth/login", json={"email": email, "password": password})

    async def request_login_code(self, email: str) -> Any:
        return await self._request("POST", "/auth/login/request-code", json={"email": email})

    async def login_with_code(self, email: str, code: str) -> Any:
        return await self._request("POST", "/auth/login/code", json={"email": email, "code": code})

    async def forgot_password(self, email: str) -> Any:
        return await self._request("POST", "/auth/password/forgot", json={"email": email})

    async def whoami(self) -> Any:
        return await self._request("POST", "/auth/whoami", auth=True)

    async def recommend_text(self, payload: dict) -> Any:
        """The coordinator wire: `detail=min` + `format=text` → compact tabular text (a `str`),
        or the usual error dict. Depth is pulled per-tool afterwards via `describe`."""
        return await self._request("POST", "/recommend", auth=True,
                                   json={**payload, "detail": "min", "format": "text"},
                                   as_text=True)

    async def describe(self, payload: dict) -> Any:
        return await self._request("POST", "/describe", auth=True, json=payload)

    async def catalog(self) -> Any:
        """The router's tool catalog (id + kind + auth + capabilities). Public; used to resolve
        which coordinator ids exist before declaring the native baseline."""
        return await self._request("GET", "/console", params={"view": "tools", "format": "json"})

    async def onboard_info(self, tool_id: str) -> Any:
        return await self._request("GET", f"/onboard/{tool_id}", auth=True)

    async def onboard(self, tool_id: str, api_key: str, label: str | None) -> Any:
        return await self._request("POST", "/onboard", auth=True,
                                   json={"tool_id": tool_id, "api_key": api_key, "label": label})

    async def execute(self, tool_id: str, query: str) -> Any:
        return await self._request("POST", "/execute", auth=True,
                                   json={"tool_id": tool_id, "query": query})

    async def read_result(self, ref: str, op: str, offset: int, limit: int,
                          path: list | None, query: str | None) -> Any:
        return await self._request("POST", f"/result/{ref}/read", auth=True,
                                   json={"op": op, "offset": offset, "limit": limit,
                                         "path": path, "query": query})

    async def list_credentials(self, user_id: str) -> Any:
        return await self._request("GET", "/credentials", auth=True, params={"user_id": user_id})

    async def report_outcome(self, payload: dict) -> Any:
        """Gated: an outcome is a per-user mutation of the flywheel, and the router attributes the
        write to the AUTHENTICATED account (it will not take a caller-supplied identity). Without
        the bearer this is a flat 401, so the whole reporting hook silently stops working."""
        return await self._request("POST", "/report_outcome", auth=True, json=payload)

    async def report_narrative(self, payload: dict) -> Any:
        return await self._request("POST", "/report_narrative", auth=True, json=payload)

    async def console(self, view: str, user_id: str) -> Any:
        return await self._request("GET", "/console", auth=True,
                                   params={"view": view, "format": "json", "user_id": user_id})

    async def facets_catalog(self) -> Any:
        return await self._request("GET", "/facets/catalog")

    async def get_preferences(self, project_id: str | None = None) -> Any:
        return await self._request("GET", "/preferences", auth=True,
                                   params={"project_id": project_id})

    async def set_preferences(self, facets: dict, project_id: str | None = None) -> Any:
        return await self._request("PUT", "/preferences", auth=True,
                                   json={"facets": facets, "project_id": project_id})

    # -- private tools (the caller's own declared tools) ---------------------
    async def list_private_tools(self, project_id: str | None = None) -> Any:
        return await self._request("GET", "/private-tools", auth=True,
                                   params={"project_id": project_id})

    async def suggest_private_regions(self, name: str, description: str | None = None) -> Any:
        return await self._request("POST", "/private-tools/suggest", auth=True,
                                   json={"name": name, "description": description})

    async def declare_private_tool(self, payload: dict) -> Any:
        return await self._request("PUT", "/private-tools", auth=True, json=payload)

    async def update_private_tool(self, tool_id: str, payload: dict) -> Any:
        return await self._request("PATCH", f"/private-tools/{tool_id}", auth=True, json=payload)

    async def set_private_stance(self, tool_id: str, stance: str,
                                 project_id: str | None = None) -> Any:
        return await self._request("POST", f"/private-tools/{tool_id}/stance", auth=True,
                                   params={"stance": stance, "project_id": project_id})

    async def delete_private_tool(self, tool_id: str, project_id: str | None = None) -> Any:
        return await self._request("DELETE", f"/private-tools/{tool_id}", auth=True,
                                   params={"project_id": project_id})

    # -- HyperFeed ----------------------------------------------------------
    async def feed(self, stream: str | None = None, since: str | None = None,
                   limit: int = 50) -> Any:
        return await self._request("GET", "/feed",
                                   params={"stream": stream, "since": since, "limit": limit})

    async def feed_streams(self) -> Any:
        return await self._request("GET", "/feed/streams")

    async def feed_digest(self, since: str | None = None, limit: int = 20) -> Any:
        return await self._request("GET", "/feed/digest", auth=True,
                                   params={"since": since, "limit": limit})

    async def feed_subscribe(self, payload: dict) -> Any:
        return await self._request("POST", "/feed/subscribe", auth=True, json=payload)

    async def feed_react(self, item_id: str, action: str) -> Any:
        return await self._request("POST", "/feed/react", auth=True,
                                   json={"item_id": item_id, "action": action})
