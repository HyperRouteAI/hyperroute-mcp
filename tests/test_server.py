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
         "whoami", "my_tools", "suggest_my_tool_regions", "declare_my_tool", "update_my_tool",
         "remove_my_tool", "my_tool_report", "my_preferred_tools", "prefer_tool",
         "update_preferred_tool", "unprefer_tool"}


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
    assert "use_own" in text
    for word in ("ready", "needs_key", "native", "own", "soon"):   # the `use` column legend
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
    lambda: server.my_tools(),
    lambda: server.my_tool_report(),
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


# -- the user's own tools ----------------------------------------------------

async def test_private_tool_verbs_require_login(fake, monkeypatch):
    """Every one is per-user state — none of them should reach the router unauthenticated."""
    monkeypatch.setattr(server, "_session", Session())
    for call in (server.my_tools(), server.my_tool_report(),
                 server.suggest_my_tool_regions("x"),
                 server.declare_my_tool("x", ["y"]),
                 server.update_my_tool("__own__:x", name="z"),
                 server.remove_my_tool("__own__:x")):
        assert (await call)["_error"] is True
    assert fake.calls == [], "a logged-out verb must not hit the router at all"


async def test_declare_sends_the_users_sentence_verbatim(fake, logged_in):
    """The trigger IS the declaration (specs/00): it goes to the router exactly as the user said
    it, and nothing about the region path runs — a rule is not a place in capability space."""
    out = await server.declare_my_tool(
        "ivanspersonalVPN", ["use it whenever I need to do a vpn connect"])
    sent = fake.last("declare_private_tool")
    assert sent["triggers"] == ["use it whenever I need to do a vpn connect"]
    assert sent["anchors"] == []
    assert not any(n == "suggest_private_regions" for n, _ in fake.calls), \
        "declaring must not spend a shared-GPU inference on wording that routes nothing"
    assert "triggers" in out["_coordinator_action"]


async def test_declare_without_a_trigger_asks_for_the_sentence(fake, logged_in):
    """The router refuses a triggerless declaration; say so in terms of the MISSING FIELD.

    Reported as a description problem, an agent rewords `description` forever against a wall it
    cannot see — which is exactly what happened when the router moved to triggers ahead of us."""
    out = await server.declare_my_tool("ivanspersonalVPN", [], description="a vpn tool")
    assert out["_error"] is True and "triggers" in out["message"]
    assert not any(n == "declare_private_tool" for n, _ in fake.calls), "nothing should be declared"


async def test_a_single_sentence_is_not_split_into_characters(fake, logged_in):
    """A model asked for a list of sentences routinely sends one sentence."""
    await server.declare_my_tool("My Search", "always use it for research")
    assert fake.last("declare_private_tool")["triggers"] == ["always use it for research"]


async def test_capabilities_are_passed_through_as_report_wording_only(fake, logged_in):
    await server.declare_my_tool("My Search", ["for papers use my thing"],
                                 capabilities=["academic_paper_search"])
    assert fake.last("declare_private_tool")["anchors"] == ["academic_paper_search"]
    assert not any(n == "suggest_private_regions" for n, _ in fake.calls)


async def test_a_description_that_maps_nowhere_still_declares(logged_in, monkeypatch):
    """A poor anchor match must never block a declaration: anchors route nothing (specs/40)."""
    c = FakeClient(suggest_private_regions={"suggestions": [
        {"id": "hotel_search_booking", "label": "Hotel booking",
         "similarity": 0.1, "in_taxonomy": False}]})
    monkeypatch.setattr(server, "_client", lambda: c)
    out = await server.declare_my_tool("thing", ["use it for the weird internal thing"],
                                       description="asdfasdf")
    assert not out.get("_error")
    assert c.last("declare_private_tool")["triggers"] == ["use it for the weird internal thing"]


async def test_declare_defaults_to_pinned_and_validates_stance(fake, logged_in):
    await server.declare_my_tool("My Search", ["use it for web search"])
    assert fake.last("declare_private_tool")["stance"] == "pinned"
    bad = await server.declare_my_tool("x", ["y"], stance="whatever")
    assert bad["_error"] is True and "stance must be" in bad["message"]


async def test_update_replaces_the_whole_trigger_list_and_refuses_an_empty_one(fake, logged_in):
    await server.update_my_tool("__own__:x", triggers=["only for work files"])
    assert fake.last("update_private_tool")["triggers"] == ["only for work files"]
    out = await server.update_my_tool("__own__:x", triggers=[" "])
    assert out["_error"] is True and "at least one trigger" in out["message"]


async def test_update_sends_only_the_fields_given(fake, logged_in):
    await server.update_my_tool("__own__:my_search", description="new words")
    sent = fake.last("update_private_tool")
    assert sent == {"tool_id": "__own__:my_search", "description": "new words"}
    assert "name" not in sent and "anchors" not in sent


async def test_update_validates_stance(fake, logged_in):
    out = await server.update_my_tool("__own__:x", stance="nope")
    assert out["_error"] is True


async def test_remove_and_list(fake, logged_in):
    assert (await server.remove_my_tool("__own__:my_search"))["deleted"] is True
    await server.my_tools()
    assert fake.last("list_private_tools") == {"project_id": None}


async def test_report_reads_the_own_console_view(fake, logged_in):
    await server.my_tool_report()
    assert fake.last("console")["view"] == "own"


async def test_instructions_teach_declaring_and_the_own_verdict():
    text = server.mcp.instructions or ""
    assert "declare_my_tool" in text
    assert "never declare one on your own initiative" in text
    # the two honesty properties the surface must convey
    assert "CONDITIONAL" in text and "UNSCORED" in text
    # the trigger model, not the retired region model
    assert "VERBATIM" in text and "context.situation" not in text
    assert "consider" in text
    assert "report_outcome` against the `__own__:" in text


# -- preferred tools ---------------------------------------------------------
async def test_preferred_tool_verbs_require_login(fake, monkeypatch):
    monkeypatch.setattr(server, "_session", Session())
    for call in (server.my_preferred_tools(), server.prefer_tool("kagi"),
                 server.update_preferred_tool("kagi_search", margin=0.2),
                 server.unprefer_tool("kagi_search")):
        assert (await call)["_error"] is True
    assert fake.calls == [], "a logged-out verb must not hit the router at all"


async def test_prefer_sends_the_users_wording_and_tells_the_agent_what_to_say(fake, logged_in):
    out = await server.prefer_tool("Kagi", note="I like it")
    sent = fake.last("prefer_tool")
    assert sent["tool"] == "Kagi" and sent["note"] == "I like it" and sent["margin"] is None
    assert "whenever it is acceptable" in out["_coordinator_action"]
    assert "0.10" in out["_coordinator_action"]


async def test_an_ambiguous_name_is_handed_back_to_ask_not_guessed(logged_in, monkeypatch):
    c = FakeClient(prefer_tool={"_error": True, "_http_status": 422, "error": "ambiguous_tool",
                                "candidates": [{"id": "perplexity_sonar", "name": "Perplexity Sonar"},
                                               {"id": "sonar_deep_research", "name": "Deep Research"}]})
    monkeypatch.setattr(server, "_client", lambda: c)
    out = await server.prefer_tool("perplexity")
    assert out["_error"] is True and len(out["candidates"]) == 2
    assert "Ask the user" in out["_coordinator_action"]


async def test_update_sends_only_the_preference_fields_given(fake, logged_in):
    await server.update_preferred_tool("kagi_search", margin=0.25)
    assert fake.last("update_preferred_tool") == {"tool_id": "kagi_search", "margin": 0.25}


async def test_unprefer_and_list(fake, logged_in):
    assert (await server.unprefer_tool("kagi_search"))["deleted"] is True
    await server.my_preferred_tools()
    assert fake.last("list_preferred_tools") == {"project_id": None}


async def test_instructions_teach_preferring_and_the_passed_over_line():
    text = server.mcp.instructions or ""
    assert "prefer_tool" in text and "Never prefer a tool on your own initiative" in text
    assert "passed over" in text and "MARGIN" in text

