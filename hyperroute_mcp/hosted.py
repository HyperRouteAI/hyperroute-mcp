from __future__ import annotations

# `hyperroute-mcp serve`: the same server over Streamable HTTP at /mcp, for clients that cannot launch
# a process on the user's machine (claude.ai, ChatGPT, n8n). One process serves every account: each
# request carries its own bearer, resolved to an account through the router and relayed on every call.
import contextvars
import hashlib
import json
import math
import os
import threading
import time
from urllib.parse import urlsplit

import httpx

from . import config

# Only make sense on the user's machine: sign-in is the connector's job, and nothing is installed.
LOCAL_TOOLS = ("setup", "use_token", "login")
DEFAULT_PORT = 8076
DEFAULT_RATE = 120.0          # the routing ceiling per account, calls per minute (specs/10)
DEFAULT_BURST = 60.0
CACHE_SECONDS = 60.0          # a revoked token stops working within this
CACHE_MAX = 10000

_bearer: contextvars.ContextVar[str | None] = contextvars.ContextVar("hyperroute_bearer", default=None)
_account: contextvars.ContextVar[dict | None] = contextvars.ContextVar("hyperroute_account", default=None)


def public_url() -> str:
    return (os.environ.get("HYPERROUTE_PUBLIC_URL") or config.base_url()).rstrip("/")


def resource_metadata_url() -> str:
    return public_url() + "/.well-known/oauth-protected-resource/mcp"


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


class RequestSession:
    """The server's session, read from the current request: never stored, never shared."""
    env_managed = True

    @property
    def api_key(self) -> str | None:
        return _bearer.get()

    @api_key.setter
    def api_key(self, value) -> None:
        pass

    @property
    def user_id(self) -> str | None:
        acct = _account.get() or {}
        return str(acct["id"]) if acct.get("id") else None

    @user_id.setter
    def user_id(self, value) -> None:
        pass

    @property
    def email(self) -> str | None:
        return (_account.get() or {}).get("email")

    @email.setter
    def email(self, value) -> None:
        pass

    @property
    def logged_in(self) -> bool:
        return bool(_bearer.get())


class NoLedger:
    """The ledger tracks one agent process on one machine for its hooks. A hosted client has no
    hooks, and a shared ledger would mix accounts, so hosted requests keep none: the router's own
    `owed:` line asks for reports."""

    def pending(self, key=None) -> list[dict]:
        return []

    def describe(self, runs) -> str:
        return ""

    def add_run(self, *a, **kw) -> None:
        pass

    def clear_item(self, item) -> None:
        pass

    def grant_pass(self, query) -> None:
        pass

    def note_outage(self) -> None:
        pass


class Accounts:

    def __init__(self, base_url: str, ttl: float = CACHE_SECONDS) -> None:
        self.base_url = base_url.rstrip("/")
        self.ttl = ttl
        self._cache: dict[str, tuple[float, dict | None]] = {}
        self._lock = threading.Lock()

    async def resolve(self, token: str) -> dict | None:
        key = hashlib.sha256(token.encode()).hexdigest()
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(f"{self.base_url}/auth/whoami", headers={"authorization": f"Bearer {token}"})
        if r.status_code in (401, 403):
            acct = None
        elif r.is_success:
            acct = r.json()
        else:
            raise RuntimeError(f"router answered {r.status_code}")
        with self._lock:
            if len(self._cache) >= CACHE_MAX:
                self._cache.clear()
            self._cache[key] = (now + self.ttl, acct)
        return acct


class Limiter:
    """A token bucket per account."""

    def __init__(self, per_minute: float, burst: float) -> None:
        self.rate = per_minute / 60.0
        self.burst = burst
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def take(self, key: str) -> float:
        """0 when the call may go; else the seconds to wait."""
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens >= 1.0:
                self._buckets[key] = (tokens - 1.0, now)
                return 0.0
            self._buckets[key] = (tokens, now)
            return (1.0 - tokens) / self.rate if self.rate > 0 else 60.0


def _bearer_from(scope) -> str | None:
    for name, value in scope.get("headers") or []:
        if name == b"authorization":
            parts = value.decode("latin-1").split(None, 1)
            if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
                return parts[1].strip()
    return None


async def _send_json(send, status: int, body: dict, headers: list[tuple[bytes, bytes]]) -> None:
    payload = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(payload)).encode()), *headers]})
    await send({"type": "http.response.body", "body": payload})


class Gate:
    """ASGI wrapper: every /mcp request needs a bearer that resolves to an account, under that
    account's rate limit. The bearer and account ride in context variables for the request only."""

    def __init__(self, app, accounts: Accounts, limiter: Limiter, path: str = "/mcp") -> None:
        self.app = app
        self.accounts = accounts
        self.limiter = limiter
        self.path = path

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope.get("path", "").rstrip("/") != self.path:
            await self.app(scope, receive, send)
            return
        token = _bearer_from(scope)
        challenge = f'Bearer resource_metadata="{resource_metadata_url()}"'
        if not token:
            await _send_json(send, 401, {"error": "unauthorized",
                                         "message": f"sign in to HyperRoute: connect this app to "
                                                    f"{public_url()}/mcp, or send a hyr_ token "
                                                    f"as Authorization: Bearer"},
                             [(b"www-authenticate", challenge.encode())])
            return
        try:
            acct = await self.accounts.resolve(token)
        except Exception:
            await _send_json(send, 503, {"error": "router_unavailable",
                                         "message": "HyperRoute is unreachable, try again shortly"},
                             [(b"retry-after", b"5")])
            return
        if not acct or not acct.get("id"):
            await _send_json(send, 401, {"error": "invalid_token",
                                         "message": "this HyperRoute sign-in is no longer valid, "
                                                    "reconnect the app"},
                             [(b"www-authenticate", (challenge + ', error="invalid_token"').encode())])
            return
        wait = self.limiter.take(str(acct["id"]))
        if wait > 0:
            await _send_json(send, 429, {"error": "rate_limited",
                                         "message": "too many HyperRoute calls, slow down"},
                             [(b"retry-after", str(max(1, math.ceil(wait))).encode())])
            return
        t1, t2 = _bearer.set(token), _account.set(acct)
        try:
            await self.app(scope, receive, send)
        finally:
            _bearer.reset(t1)
            _account.reset(t2)


def configure():
    """Turn the server module into the hosted one: per-request session, no ledger, no local tools,
    hosted instructions. Process-wide: `serve` calls it once at start."""
    from . import server
    server._HOSTED = True
    server._session = RequestSession()
    server._ledger = NoLedger()
    for name in LOCAL_TOOLS:
        try:
            server.mcp.remove_tool(name)
        except Exception:
            pass
    server.mcp._lowlevel_server.instructions = server.instructions(hosted=True)
    return server.mcp


def build_app(accounts: Accounts | None = None, limiter: Limiter | None = None):
    from mcp.server.transport_security import TransportSecuritySettings
    mcp = configure()
    host = urlsplit(public_url()).netloc
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[host, "127.0.0.1:*", "localhost:*", "[::1]:*"],
        allowed_origins=[public_url(), "http://127.0.0.1:*", "http://localhost:*"])
    app = mcp.streamable_http_app(stateless_http=True, transport_security=security)
    return Gate(app, accounts or Accounts(config.base_url()),
                limiter or Limiter(_float_env("HYPERROUTE_MCP_RATE", DEFAULT_RATE),
                                   _float_env("HYPERROUTE_MCP_BURST", DEFAULT_BURST)))


def serve(argv: list[str]) -> int:
    import uvicorn
    host, port = "127.0.0.1", DEFAULT_PORT
    args = list(argv)
    while args:
        a = args.pop(0)
        if a == "--host" and args:
            host = args.pop(0)
        elif a == "--port" and args:
            port = int(args.pop(0))
    uvicorn.run(build_app(), host=host, port=port, proxy_headers=True, forwarded_allow_ips="127.0.0.1",
                timeout_keep_alive=75, log_level=os.environ.get("HYPERROUTE_LOG_LEVEL", "info"))
    return 0
