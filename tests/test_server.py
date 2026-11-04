import httpx
import pytest

from hyperroute_mcp import server, tokenstore
from hyperroute_mcp.client import Session, _unwrap

from .conftest import FakeClient

TOOLS = {"session_info", "setup", "use_token", "login", "run", "ranking", "describe", "onboard",
         "connect_info", "report_outcome", "report_narrative", "get_preferences", "set_preferences",
         "fetch_result", "console", "my_tools", "declare_my_tool", "update_my_tool", "remove_my_tool",
         "my_preferred_tools", "prefer_tool", "unprefer_tool"}


@pytest.fixture
def fake(monkeypatch):
    c = FakeClient()
    monkeypatch.setattr(server, "_client", lambda: c)
    monkeypatch.setattr(server, "_client_name", lambda ctx=None: "claude-code")
    monkeypatch.setattr(server, "_session", Session("hyr_test"))
    return c


@pytest.fixture
def logged_in(monkeypatch):
    monkeypatch.setattr(server, "_session", Session("hyr_test"))


@pytest.fixture(autouse=True)
def _quiet_notices():
    server._told.update({"setup", "upgrade", "unsupported"})
    yield
    server._told.clear()


async def test_notice_rides_the_first_result_once(fake, monkeypatch):
    server._told.clear()
    monkeypatch.setattr(server, "_product", lambda ctx: "claude_code")
    first = await server.run(["q"])
    second = await server.run(["q"])
    assert "note: HyperRoute setup is not finished" in first
    assert "note:" not in second


async def test_every_tool_is_registered():
    assert {t.name for t in await server.mcp.list_tools()} == TOOLS


class _Info:
    def __init__(self, name):
        self.name = name


class _Ctx:

    def __init__(self, **params):
        self.session = type("S", (), {"client_params": type("P", (), params)()})()


def test_client_name_reads_the_modern_field():
    assert server._client_name(_Ctx(client_info=_Info("claude-code"))) == "claude-code"


def test_client_name_reads_the_legacy_field():
    assert server._client_name(_Ctx(clientInfo=_Info("claude-code"))) == "claude-code"


@pytest.mark.parametrize("ctx", [None, _Ctx(), _Ctx(client_info=None)])
def test_client_name_is_none_when_unavailable(ctx):
    assert server._client_name(ctx) is None


async def test_describe_defaults_to_about(fake):
    await server.describe("brave_search")
    assert fake.last("describe")["sections"] == ["about"]


async def test_describe_forwards_the_route_relative_query(fake):
    await server.describe("brave_search", sections=["facets", "evidence"], query="q")
    payload = fake.last("describe")
    assert payload["sections"] == ["facets", "evidence"] and payload["query"] == "q"
    assert payload["context"]["native_tools"]


@pytest.mark.parametrize("call", [
    lambda: server.ranking("r_1", 1),
    lambda: server.onboard("brave_search", "k"),
    lambda: server.connect_info("brave_search"),
    lambda: server.get_preferences(),
    lambda: server.set_preferences({}),
    lambda: server.fetch_result("ref"),
    lambda: server.my_tools(),
    lambda: server.report_outcome("r_1/1", "full"),
])
async def test_gated_tools_refuse_when_logged_out(monkeypatch, call):
    monkeypatch.setattr(server, "_session", Session())
    out = await call()
    assert out["_error"] and "not logged in" in out["message"].lower()


async def test_run_refuses_when_logged_out(fake, monkeypatch):
    monkeypatch.setattr(server, "_session", Session())
    out = await server.run(["q"])
    assert out.startswith("STOP") and "use_token" in out and "`login`" in out and "login_link" not in out


async def _answer(value):
    return value


async def test_a_401_forgets_the_cached_token(monkeypatch):
    monkeypatch.setattr(server, "_session", Session("hyr_stale"))
    out = await server._authed(_answer({"_error": True, "_http_status": 401}))
    assert "no longer valid" in out["message"]
    assert not server._session.logged_in


async def test_session_info_surfaces_the_declared_baseline(fake, logged_in):
    info = await server.session_info()
    assert info["router"] == "ok" and info["account"]["email"] == "a@x.io"
    assert info["logged_in"] is True
    assert info["api_key"] == "hyr_test"
    assert info["mcp_client"] == "claude-code"
    assert info["native_tools"] == ["claude_code_opus_deep", "claude_code_sonnet_quick"]


async def test_session_info_masks_a_real_length_token(fake, monkeypatch):
    monkeypatch.setattr(server, "_session", Session("hyr_0123456789abcdef"))
    info = await server.session_info()
    assert info["api_key"] == "hyr_0123…cdef"
    assert "0123456789abcdef" not in info["api_key"]


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


async def test_private_tool_verbs_require_login(fake, monkeypatch):
    monkeypatch.setattr(server, "_session", Session())
    for call in (server.my_tools(),
                 server.declare_my_tool("x", ["y"]),
                 server.update_my_tool("__own__:x", name="z"),
                 server.remove_my_tool("__own__:x")):
        assert (await call)["_error"] is True
    assert fake.calls == [], "a logged-out verb must not hit the router at all"


async def test_declare_sends_the_users_sentence_verbatim(fake, logged_in):
    out = await server.declare_my_tool(
        "ivanspersonalVPN", ["use it whenever I need to do a vpn connect"])
    sent = fake.last("declare_private_tool")
    assert sent["triggers"] == ["use it whenever I need to do a vpn connect"]
    assert sent["anchors"] == []
    assert not any(n == "suggest_private_regions" for n, _ in fake.calls), \
        "declaring must not spend a shared-GPU inference on wording that routes nothing"
    assert "triggers" in out["_coordinator_action"]


async def test_declare_without_a_trigger_asks_for_the_sentence(fake, logged_in):
    out = await server.declare_my_tool("ivanspersonalVPN", [], description="a vpn tool")
    assert out["_error"] is True and "triggers" in out["message"]
    assert not any(n == "declare_private_tool" for n, _ in fake.calls), "nothing should be declared"


async def test_a_single_sentence_is_not_split_into_characters(fake, logged_in):
    await server.declare_my_tool("My Search", "always use it for research")
    assert fake.last("declare_private_tool")["triggers"] == ["always use it for research"]


async def test_capabilities_are_passed_through_as_report_wording_only(fake, logged_in):
    await server.declare_my_tool("My Search", ["for papers use my thing"],
                                 capabilities=["academic_paper_search"])
    assert fake.last("declare_private_tool")["anchors"] == ["academic_paper_search"]
    assert not any(n == "suggest_private_regions" for n, _ in fake.calls)


async def test_a_description_that_maps_nowhere_still_declares(logged_in, monkeypatch):
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


async def test_my_tools_carries_the_own_report(fake, logged_in):
    out = await server.my_tools()
    assert fake.last("console")["view"] == "own" and "report" in out


async def test_instructions_teach_declaring_and_the_own_verdict():
    text = server._INSTRUCTIONS
    assert "declare_my_tool" in text and "verbatim" in text
    assert "Never declare on your own" in text
    assert "use_own" in text


async def test_preferred_tool_verbs_require_login(fake, monkeypatch):
    monkeypatch.setattr(server, "_session", Session())
    for call in (server.my_preferred_tools(), server.prefer_tool("kagi"),
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


async def test_unprefer_and_list(fake, logged_in):
    assert (await server.unprefer_tool("kagi_search"))["deleted"] is True
    await server.my_preferred_tools()
    assert fake.last("list_preferred_tools") == {"project_id": None}


async def test_instructions_teach_preferring_and_the_passed_over_line():
    text = server._INSTRUCTIONS
    assert "prefer_tool" in text and "Never prefer on your own" in text and "margin" in text


async def test_setup_plans_then_removes(fake, logged_in, monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("HYPERROUTE_HOME", str(tmp_path / "hr"))
    monkeypatch.setattr(server, "_product", lambda ctx: "claude_code")
    plan = await server.setup()
    assert plan["changes"] == 4 and "apply=true" in plan["next"]
    done = await server.setup(apply=True, confirmed=True)
    assert done["applied"] is True and done["approved_by"] == "chat"
    assert (await server.setup(remove=True))["would_remove"]
    assert sorted((await server.setup(remove=True, apply=True))["removed"]) == [
        "report-check", "session-start", "subagent-start", "web-guard"]


async def test_instructions_open_with_the_rules():
    assert server.mcp.instructions == server._INSTRUCTIONS
    assert server._INSTRUCTIONS.startswith("# HyperRoute: hard rules")


async def test_instructions_are_short():
    text = server.mcp.instructions or ""
    assert "run" in text and "declare_my_tool" in text and "prefer_tool" in text
    assert "recommend" not in text and "execute" not in text
    assert len(text) < 4000


def _run_answer(items, run="r_1", reports=None):
    return {"run": run, "items": items, "reports": reports, "text": f"run {run}"}


async def test_run_sends_items_and_returns_the_text(monkeypatch, logged_in):
    c = FakeClient(run=_run_answer([{"action": "run", "tool": "exa", "session_id": "s1",
                                     "report": "on_failure", "stakes": "commodity"}]))
    monkeypatch.setattr(server, "_client", lambda: c)
    monkeypatch.setattr(server, "_client_name", lambda ctx=None: "claude-code")
    out = await server.run(["usd to jpy on 2023-01-02", {"query": "eiffel tower coordinates", "facets": None}])
    sent = c.last("run")
    assert sent["items"] == [{"query": "usd to jpy on 2023-01-02"}, {"query": "eiffel tower coordinates"}]
    assert sent["context"]["native_tools"]
    assert out == "run r_1"
    from hyperroute_mcp import ledger
    assert ledger.pending() == []                               # on_failure owes nothing


async def test_an_owed_report_rides_the_next_run_and_high_stakes_hold(monkeypatch, logged_in):
    from hyperroute_mcp import ledger
    c = FakeClient(run=_run_answer([{"action": "run", "tool": "factcheck", "session_id": "s1",
                                     "report": "always", "stakes": "high"}]))
    monkeypatch.setattr(server, "_client", lambda: c)
    monkeypatch.setattr(server, "_client_name", lambda ctx=None: "claude-code")
    await server.run(["is the moon landing faked"])
    assert [r["item"] for r in ledger.pending()] == ["r_1/1"]
    held = await server.run(["next"])
    assert held.startswith("REPORT FIRST") and "r_1/1" in held
    c.answers["run"] = _run_answer([], run="r_2", reports={"accepted": 1, "failed": []})
    await server.run(["next"], reports=[{"item": "r_1/1", "score": "full"}])
    assert c.last("run")["reports"] == [{"item": "r_1/1", "score": "full"}]
    assert ledger.pending() == []


async def test_report_outcome_by_item(monkeypatch, logged_in):
    from hyperroute_mcp import ledger
    ledger.add_run("exa", "r_1/2")
    c = FakeClient(run={"run": "r_9", "items": [], "reports": {"accepted": 1, "failed": []}})
    monkeypatch.setattr(server, "_client", lambda: c)
    assert await server.report_outcome("r_1/2", "partial", reason="missing_data") == {"ok": True}
    assert c.last("run") == {"reports": [{"item": "r_1/2", "score": "partial", "reason": "missing_data"}]}
    assert ledger.pending() == []


async def test_a_do_yourself_item_grants_a_pass(monkeypatch, logged_in):
    from hyperroute_mcp import ledger
    c = FakeClient(run=_run_answer([{"action": "do_yourself", "query": "latest react version"}]))
    monkeypatch.setattr(server, "_client", lambda: c)
    monkeypatch.setattr(server, "_client_name", lambda ctx=None: "claude-code")
    await server.run(["latest react version"])
    assert ledger.take_pass('{"query": "react latest version"}')


async def test_a_useless_report_reopens_the_item_and_grants_its_pass(monkeypatch, logged_in):
    from hyperroute_mcp import ledger
    ledger.add_run("nominatim", "r_1/7")
    reopened = {"run": "r_9", "text": "run r_9\n[1] do it yourself, for r_1/7 (nominatim useless)",
                "items": [{"action": "do_yourself", "query": "geocode 350 Fifth Avenue New York",
                           "reopens": "r_1/7"}],
                "reports": {"accepted": 1, "failed": []}}
    monkeypatch.setattr(server, "_client", lambda: FakeClient(run=reopened))
    out = await server.report_outcome("r_1/7", "useless", reason="wrong_output")
    assert out == {"ok": True, "reopened": reopened["text"]}
    assert ledger.pending() == []
    assert ledger.take_pass('{"query": "350 Fifth Avenue New York latitude"}')


async def test_report_outcome_passes_the_users_thumbs(monkeypatch, logged_in):
    c = FakeClient(run={"run": "r_9", "items": [], "reports": {"accepted": 1, "failed": []}})
    monkeypatch.setattr(server, "_client", lambda: c)
    await server.report_outcome(item="r_1/2", score="full", satisfied=True)
    assert c.last("run") == {"reports": [{"item": "r_1/2", "score": "full", "satisfied": True}]}


async def test_onboard_passes_a_key_or_several_values(monkeypatch, logged_in):
    seen = []

    async def fake(self, tool_id, api_key, label=None, values=None):
        seen.append((tool_id, api_key, label, values))
        return {"ok": True}

    monkeypatch.setattr(server.HyperRouteClient, "onboard", fake)
    await server.onboard("omdb", "key1")
    await server.onboard("trello", values={"TRELLO_API_KEY": "a", "TRELLO_TOKEN": "b"})
    assert seen == [("omdb", "key1", None, None),
                    ("trello", None, None, {"TRELLO_API_KEY": "a", "TRELLO_TOKEN": "b"})]


async def test_onboard_surfaces_a_refusal_naming_the_fields(monkeypatch, logged_in):
    detail = {"ok": False, "status": "invalid", "reason": "missing_values",
              "missing": ["TRELLO_TOKEN"], "fields": ["TRELLO_API_KEY", "TRELLO_TOKEN"],
              "message": "This tool needs these values, sent in `values`: TRELLO_API_KEY, TRELLO_TOKEN."}

    async def fake(self, tool_id, api_key, label=None, values=None):
        return _unwrap(httpx.Response(409, json={"detail": detail}, request=httpx.Request("POST", "http://t/")))

    monkeypatch.setattr(server.HyperRouteClient, "onboard", fake)
    out = await server.onboard("trello", values={"TRELLO_API_KEY": "a"})
    assert out["_error"] and out["missing"] == ["TRELLO_TOKEN"] and out["status"] == "invalid"
