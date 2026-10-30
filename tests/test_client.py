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


async def test_ranking_returns_text_and_is_authenticated(patched):
    import json

    t = patched(httpx.Response(200, text="run r_1 item 1\n→ exa"))
    out = await HyperRouteClient("http://router.test", Session("hyr_abc")).ranking("r_1", 1)
    assert json.loads(t.request.content) == {"run": "r_1", "item": 1, "evidence": False}
    assert t.request.headers["authorization"] == "Bearer hyr_abc"
    assert out == "run r_1 item 1\n→ exa"


async def test_ranking_still_returns_a_dict_on_an_error(patched):
    patched(httpx.Response(404, json={"detail": {"error": "unknown_run"}}))
    out = await HyperRouteClient("http://router.test", Session("hyr_abc")).ranking("r_x", 1)
    assert out["_error"] and out["error"] == "unknown_run"


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


async def _capture(method, *args, **kwargs):
    t = _Transport(_resp(200, {"ok": True}))
    c = HyperRouteClient("http://router.test", Session("hyr_k"), timeout=5)
    import httpx as _httpx
    real = _httpx.AsyncClient

    def _patched(*a, **kw):
        return real(*a, **{**kw, "transport": t})
    _httpx.AsyncClient = _patched
    try:
        await getattr(c, method)(*args, **kwargs)
    finally:
        _httpx.AsyncClient = real
    return t.request


async def test_report_narrative_is_authenticated():
    req = await _capture("report_narrative", {"text": "x"})
    assert req.headers.get("authorization") == "Bearer hyr_k"


async def test_private_tool_calls_are_authenticated_and_well_addressed():
    req = await _capture("declare_private_tool", {"name": "n", "anchors": ["a"]})
    assert req.method == "PUT" and req.url.path == "/private-tools"
    assert req.headers.get("authorization") == "Bearer hyr_k"

    req = await _capture("update_private_tool", "__own__:my_search", {"description": "d"})
    assert req.method == "PATCH" and req.url.path == "/private-tools/__own__:my_search"

    req = await _capture("delete_private_tool", "__own__:my_search")
    assert req.method == "DELETE" and req.url.path == "/private-tools/__own__:my_search"


async def test_run_waits_longer_than_other_calls(monkeypatch):
    seen = []
    real = httpx.AsyncClient

    def spy(*a, **kw):
        seen.append(kw.get("timeout"))
        rest = {k: v for k, v in kw.items() if k != "transport"}
        return real(*a, transport=_Transport(_resp(200, {"ok": True})), **rest)

    monkeypatch.setattr(httpx, "AsyncClient", spy)
    c = HyperRouteClient("http://t", Session("k"), timeout=30)
    await c.run({"items": [{"query": "q"}]})
    await c.health()
    assert seen[-2] >= 900 and seen[-1] == 30


async def test_path_arguments_stay_in_their_segment(patched):
    t = patched(httpx.Response(200, json={}))
    c = HyperRouteClient("http://router.test", Session("hyr_x"))
    await c.read_result("../auth/tokens#", "slice", 0, 10, None, None)
    assert t.request.url.raw_path == b"/result/..%2Fauth%2Ftokens%23/read"
    await c.onboard_info("__own__:a/b")
    assert t.request.url.raw_path == b"/onboard/__own__%3Aa%2Fb"
