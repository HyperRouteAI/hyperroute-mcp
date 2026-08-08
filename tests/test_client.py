"""The HTTP client: error bodies become actionable dicts (never exceptions), and the coordinator
wire asks for the lean text projection."""

import httpx
import pytest

from hyperroute_mcp.client import HyperRouteClient, Session, _unwrap


def _resp(status: int, json_body=None, text: str | None = None) -> httpx.Response:
    if text is not None:
        return httpx.Response(status, text=text, request=httpx.Request("POST", "http://t/"))
    return httpx.Response(status, json=json_body, request=httpx.Request("POST", "http://t/"))


def test_unwrap_success_passes_through():
    assert _unwrap(_resp(200, {"ok": True})) == {"ok": True}


def test_unwrap_flattens_a_structured_detail():
    out = _unwrap(_resp(400, {"detail": {"error": "needs_onboard", "tool_id": "brave_search"}}))
    assert out == {"_error": True, "_http_status": 400,
                   "error": "needs_onboard", "tool_id": "brave_search"}


def test_unwrap_keeps_a_string_detail_as_a_message():
    assert _unwrap(_resp(401, {"detail": "nope"})) == {
        "_error": True, "_http_status": 401, "message": "nope"}


def test_unwrap_survives_a_non_json_error_body():
    out = _unwrap(_resp(502, text="<html>bad gateway</html>"))
    assert out["_error"] and out["_http_status"] == 502


class _Transport(httpx.AsyncBaseTransport):
    """Captures the outgoing request and replays one canned response."""

    def __init__(self, response: httpx.Response):
        self.response, self.request = response, None

    async def handle_async_request(self, request):
        self.request = request
        self.response.request = request
        return self.response


@pytest.fixture
def patched(monkeypatch):
    def _install(response):
        transport = _Transport(response)
        real = httpx.AsyncClient

        def factory(*a, **kw):
            return real(*a, **{**kw, "transport": transport})

        monkeypatch.setattr(httpx, "AsyncClient", factory)
        return transport
    return _install


async def test_recommend_text_requests_the_min_text_wire(patched):
    import json

    t = patched(httpx.Response(200, text="session: s-1\nact: execute('x', <query>)"))
    session = Session("hyr_abc")
    out = await HyperRouteClient("http://router.test", session).recommend_text({"query": "q"})

    body = json.loads(t.request.content)
    assert body["detail"] == "min" and body["format"] == "text"
    assert t.request.headers["authorization"] == "Bearer hyr_abc"
    assert isinstance(out, str) and out.startswith("session: s-1")


async def test_recommend_text_still_returns_a_dict_on_an_error(patched):
    patched(httpx.Response(503, json={"detail": {"error": "no_bundle"}}))
    out = await HyperRouteClient("http://router.test", Session()).recommend_text({"query": "q"})
    assert out["_error"] and out["error"] == "no_bundle"


async def test_unreachable_router_is_reported_not_raised(monkeypatch):
    class Dead(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("refused", request=request)

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **kw: real(*a, **{**kw, "transport": Dead()}))
    out = await HyperRouteClient("http://router.test", Session()).health()
    assert out["_error"] and "could not reach router" in out["message"]


async def test_none_fields_are_dropped_so_router_defaults_apply(patched):
    import json

    t = patched(httpx.Response(200, json={}))
    await HyperRouteClient("http://router.test", Session("k")).onboard("t", "key", label=None)
    assert json.loads(t.request.content) == {"tool_id": "t", "api_key": "key"}


async def test_no_bearer_is_sent_when_logged_out(patched):
    t = patched(httpx.Response(200, json={}))
    await HyperRouteClient("http://router.test", Session()).whoami()
    assert "authorization" not in t.request.headers
