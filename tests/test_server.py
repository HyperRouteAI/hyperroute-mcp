"""The MCP tool layer: what gets sent to the router, what gets handed back to the coordinator,
and the login gate on every per-user verb."""

import pytest

from hyperroute_mcp import server, tokenstore
from hyperroute_mcp.client import Session

from .conftest import FakeClient

TOOLS = {"session_info", "health", "recommend", "describe", "execute", "onboard", "connect_info",
         "report_outcome", "report_narrative", "facets_catalog", "get_preferences",
         "set_preferences", "list_credentials", "fetch_result", "console", "use_token",
         "register", "verify", "login", "login_link", "verify_login", "forgot_password",
         "whoami", "hyperfeed", "hyperfeed_digest", "hyperfeed_subscribe", "hyperfeed_react"}


@pytest.fixture
def fake(monkeypatch):
    """Route every tool through a FakeClient and pretend we run inside Claude Code."""
    c = FakeClient()
    monkeypatch.setattr(server, "_client", lambda: c)
    monkeypatch.setattr(server, "_client_name", lambda ctx=None: "claude-code")
    return c


@pytest.fixture
def logged_in(monkeypatch):
    monkeypatch.setattr(server, "_session", Session("hyr_test"))


# -- registration ------------------------------------------------------------
async def test_every_tool_is_registered():
    assert {t.name for t in await server.mcp.list_tools()} == TOOLS


async def test_instructions_carry_the_hard_rule_and_the_native_exception():
    text = server.mcp.instructions or ""
    assert "NEVER permitted to perform an external task on your own" in text
    assert "use_native" in text
    for word in ("ready", "needs_key", "native", "soon"):     # the `use` column legend
        assert word in text


# -- reading the client's identity off the handshake -------------------------
class _Info:
    def __init__(self, name):
        self.name = name


class _Ctx:
    """Stands in for the injected Context. The SDK renamed this field between majors, and the
    failure is SILENT — an unread client name means no baseline and an external tool wins every
    task — so both spellings are pinned here."""

    def __init__(self, **params):
        self.session = type("S", (), {"client_params": type("P", (), params)()})()


def test_client_name_reads_the_modern_field():
    assert server._client_name(_Ctx(client_info=_Info("claude-code"))) == "claude-code"


def test_client_name_reads_the_legacy_field():
    assert server._client_name(_Ctx(clientInfo=_Info("claude-code"))) == "claude-code"


@pytest.mark.parametrize("ctx", [None, _Ctx(), _Ctx(client_info=None)])
def test_client_name_is_none_when_unavailable(ctx):
    assert server._client_name(ctx) is None


# -- recommend: the lean wire + the declaration ------------------------------
async def test_recommend_returns_the_routers_text_verbatim(fake):
    out = await server.recommend("find recent RCTs on statins")
    assert isinstance(out, str)
    assert out == "session: s-1\nact: execute('brave_search', <query>)"


async def test_recommend_declares_the_native_baseline(fake):
    await server.recommend("q")
    ctx = fake.last("recommend_text")["context"]
    assert ctx["native_tools"] == ["claude_code_opus_deep", "claude_code_sonnet_quick"]


async def test_recommend_keeps_the_declaration_under_a_caller_context(fake):
    await server.recommend("q", context={"native_tools": [], "usage": {"monthly_queries": 5}})
    ctx = fake.last("recommend_text")["context"]
    assert ctx["native_tools"] == ["claude_code_opus_deep", "claude_code_sonnet_quick"]
    assert ctx["usage"] == {"monthly_queries": 5}


async def test_recommend_passes_facets_through_and_omits_unset_knobs(fake):
    await server.recommend("q", facets={"price": {"weight": 2}})
    payload = fake.last("recommend_text")
    assert payload["facets"] == {"price": {"weight": 2}}
    assert "n_runner_ups" not in payload


async def test_recommend_renders_an_error_as_text(monkeypatch):
    c = FakeClient(recommend_text={"_error": True, "_http_status": 503,
                                   "message": "no model bundle loaded"})
    monkeypatch.setattr(server, "_client", lambda: c)
    monkeypatch.setattr(server, "_client_name", lambda ctx=None: "claude-code")
    out = await server.recommend("q")
    assert out == "error: no model bundle loaded  (HTTP 503)"


# -- describe: progressive disclosure ----------------------------------------
async def test_describe_defaults_to_about(fake):
    await server.describe("brave_search")
    assert fake.last("describe")["sections"] == ["about"]


async def test_describe_forwards_the_route_relative_query(fake):
    await server.describe("brave_search", sections=["facets", "evidence"], query="q")
    payload = fake.last("describe")
    assert payload["sections"] == ["facets", "evidence"] and payload["query"] == "q"
    assert payload["context"]["native_tools"]          # same baseline as the route it describes


# -- the login gate ----------------------------------------------------------
@pytest.mark.parametrize("call", [
    lambda: server.execute("brave_search", "q"),
    lambda: server.onboard("brave_search", "k"),
    lambda: server.connect_info("brave_search"),
    lambda: server.list_credentials(),
    lambda: server.whoami(),
    lambda: server.get_preferences(),
    lambda: server.set_preferences({}),
    lambda: server.fetch_result("ref"),
    lambda: server.hyperfeed_digest(),
    lambda: server.hyperfeed_react("i", "up"),
])
async def test_gated_tools_refuse_when_logged_out(monkeypatch, call):
    monkeypatch.setattr(server, "_session", Session())
    out = await call()
    assert out["_error"] and "not logged in" in out["message"]


async def test_execute_runs_once_logged_in(fake, logged_in):
    assert await server.execute("brave_search", "q") == {"result": "ok"}
    assert fake.last("execute") == {"tool_id": "brave_search", "query": "q"}


async def _answer(value):
    return value


async def test_a_401_forgets_the_cached_token(monkeypatch):
    monkeypatch.setattr(server, "_session", Session("hyr_stale"))
    out = await server._authed(_answer({"_error": True, "_http_status": 401}))
    assert "no longer valid" in out["message"]
    assert not server._session.logged_in


# -- session_info ------------------------------------------------------------
async def test_session_info_surfaces_the_declared_baseline(fake, logged_in):
    info = await server.session_info()
    assert info["logged_in"] is True
    assert info["api_key"] == "hyr_test"                       # short token: shown, not mangled
    assert info["mcp_client"] == "claude-code"
    assert info["native_tools"] == ["claude_code_opus_deep", "claude_code_sonnet_quick"]


async def test_session_info_masks_a_real_length_token(fake, monkeypatch):
    monkeypatch.setattr(server, "_session", Session("hyr_0123456789abcdef"))
    info = await server.session_info()
    assert info["api_key"] == "hyr_0123…cdef"
    assert "0123456789abcdef" not in info["api_key"]


# -- the token cache ---------------------------------------------------------
def test_token_cache_round_trips_per_router():
    tokenstore.save("http://a.test", "hyr_a", "1", "a@x.io")
    tokenstore.save("http://b.test", "hyr_b", "2", "b@x.io")
    assert tokenstore.load("http://a.test")["api_key"] == "hyr_a"
    tokenstore.clear("http://a.test")
    assert tokenstore.load("http://a.test") is None
    assert tokenstore.load("http://b.test")["api_key"] == "hyr_b"


def test_token_cache_is_private():
    import os
    import stat
    tokenstore.save("http://c.test", "hyr_c", None, None)
    mode = stat.S_IMODE(os.stat(os.environ["HYPERROUTE_TOKEN_FILE"]).st_mode)
    assert mode == 0o600
