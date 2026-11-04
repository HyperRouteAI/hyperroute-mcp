from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from . import config, install
from .caller import HEADER, Caller


class Session:

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key
        self.user_id: str | None = None
        self.email: str | None = None
        self.env_managed = False

    @property
    def logged_in(self) -> bool:
        return bool(self.api_key)


def _seg(value: Any) -> str:
    return quote(str(value), safe="")


def _unwrap(r: httpx.Response) -> Any:
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

    def __init__(self, base_url: str, session: Session, timeout: float = 30.0,
                 caller: Caller | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session
        self._timeout = timeout
        self.caller = caller

    def _headers(self, auth: bool) -> dict[str, str]:
        h = {"content-type": "application/json", install.REQUEST_HEADER: install.version()}
        if auth and self.session.api_key:
            h["authorization"] = f"Bearer {self.session.api_key}"
        return h
    async def _request(self, method: str, path: str, *, auth: bool = False,
                       json: dict | None = None, params: dict | None = None,
                       as_text: bool = False, tag: bool = False,
                       timeout: float | None = None) -> Any:
        if json is not None:
            json = {k: v for k, v in json.items() if v is not None}
        sent = None
        if tag and self.caller is not None and json is not None:
            try:
                await self.caller.load(self.collectors)
                block, sent = await self.caller.block()
                if block:
                    json["caller"] = block
            except Exception:
                sent = None
        if params is not None:
            params = {k: v for k, v in params.items() if v is not None}
        try:
            async with httpx.AsyncClient(timeout=timeout or self._timeout) as c:
                r = await c.request(method, f"{self.base_url}{path}",
                                    headers=self._headers(auth), json=json, params=params)
        except httpx.RequestError as e:
            return {"_error": True, "message": f"could not reach router at {self.base_url}: {e}"}
        install.note_router(r.headers.get(install.RESPONSE_HEADER))
        if sent is not None:
            self.caller.ack(r.headers.get(HEADER), sent, r.is_success)
            if self.caller.ref:
                try:
                    from . import ledger
                    ledger.set_caller(self.caller.ref)
                except Exception:
                    pass
        if as_text and r.is_success:
            return r.text
        return _unwrap(r)
    async def collectors(self) -> Any:
        async with httpx.AsyncClient(timeout=1.5) as c:
            r = await c.get(f"{self.base_url}/caller/collectors")
        return r.json() if r.is_success else None
    async def health(self) -> Any:
        return await self._request("GET", "/health")
    async def login(self, email: str, password: str) -> Any:
        return await self._request("POST", "/auth/login", json={"email": email, "password": password})
    async def whoami(self) -> Any:
        return await self._request("POST", "/auth/whoami", auth=True)
    async def run(self, payload: dict) -> Any:
        return await self._request("POST", "/run", auth=True, json=payload, tag=True,
                                   timeout=config.execute_timeout())
    async def ranking(self, run: str, item: int, evidence: bool = False) -> Any:
        return await self._request("POST", "/ranking", auth=True, as_text=not evidence,
                                   json={"run": run, "item": item, "evidence": evidence})
    async def describe(self, payload: dict) -> Any:
        return await self._request("POST", "/describe", auth=True, json=payload)
    async def catalog(self) -> Any:
        return await self._request("GET", "/console", params={"view": "tools", "format": "json"})
    async def onboard_info(self, tool_id: str) -> Any:
        return await self._request("GET", f"/onboard/{_seg(tool_id)}", auth=True)
    async def onboard(self, tool_id: str, api_key: str | None, label: str | None,
                      values: dict | None = None) -> Any:
        return await self._request("POST", "/onboard", auth=True,
                                   json={"tool_id": tool_id, "api_key": api_key, "label": label, "values": values})
    async def read_result(self, ref: str, op: str, offset: int, limit: int,
                          path: list | None, query: str | None) -> Any:
        return await self._request("POST", f"/result/{_seg(ref)}/read", auth=True,
                                   json={"op": op, "offset": offset, "limit": limit,
                                         "path": path, "query": query})
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
    async def list_private_tools(self, project_id: str | None = None) -> Any:
        return await self._request("GET", "/private-tools", auth=True,
                                   params={"project_id": project_id})
    async def declare_private_tool(self, payload: dict) -> Any:
        return await self._request("PUT", "/private-tools", auth=True, json=payload)
    async def update_private_tool(self, tool_id: str, payload: dict) -> Any:
        return await self._request("PATCH", f"/private-tools/{_seg(tool_id)}", auth=True, json=payload)
    async def delete_private_tool(self, tool_id: str, project_id: str | None = None) -> Any:
        return await self._request("DELETE", f"/private-tools/{_seg(tool_id)}", auth=True,
                                   params={"project_id": project_id})
    async def list_preferred_tools(self, project_id: str | None = None) -> Any:
        return await self._request("GET", "/preferred-tools", auth=True,
                                   params={"project_id": project_id})
    async def prefer_tool(self, payload: dict) -> Any:
        return await self._request("PUT", "/preferred-tools", auth=True, json=payload)
    async def delete_preferred_tool(self, tool_id: str, project_id: str | None = None) -> Any:
        return await self._request("DELETE", f"/preferred-tools/{_seg(tool_id)}", auth=True,
                                   params={"project_id": project_id})
