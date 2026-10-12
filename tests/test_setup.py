import json

import pytest

from hyperroute_mcp import agents, hooks, install, ledger


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("HYPERROUTE_HOME", str(tmp_path / "hr"))
    monkeypatch.setattr(install, "_router", {"latest": None, "min": None})
    return tmp_path


def _settings():
    return json.loads(agents.claude_settings().read_text())


def test_versions_compare():
    assert install.older("0.4.0", "0.10.0")
    assert not install.older("0.5.0", "0.5.0")
    assert not install.older("0.5.0", None)


def test_router_header_wins_for_latest():
    install.note_router("latest=9.9.9; min=0.1.0")
    assert install.latest() == "9.9.9"
    assert install.router_versions()["min"] == "0.1.0"


def test_hook_outputs(monkeypatch):
    monkeypatch.setattr(hooks, "logged_in", lambda: True)
    s = hooks.output("session-start", ["--server", "hr"])
    assert s["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "mcp__hr__recommend" in s["hookSpecificOutput"]["additionalContext"]
    sub = hooks.output("subagent-start", [])
    assert sub["hookSpecificOutput"]["hookEventName"] == "SubagentStart"
    remind = hooks.output("web-guard", [], {"tool_name": "WebSearch"})
    assert "permissionDecision" not in remind["hookSpecificOutput"]
    block = hooks.output("web-guard", ["--block"], {"tool_name": "WebFetch"})
    assert block["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert hooks.output("nope", []) is None


def test_plan_changes_nothing():
    plan = agents.claude_plan()
    assert [s["state"] for s in plan["steps"]] == ["add", "add", "add", "add"]
    assert not agents.claude_settings().exists()


def test_apply_keeps_user_hooks_and_is_idempotent():
    path = agents.claude_settings()
    path.parent.mkdir(parents=True)
    mine = {"type": "command", "command": "echo mine"}
    path.write_text(json.dumps({"model": "opus", "hooks": {"SessionStart": [{"hooks": [mine]}]}}))
    agents.claude_apply()
    agents.claude_apply()
    s = _settings()
    assert s["model"] == "opus"
    starts = [h for g in s["hooks"]["SessionStart"] for h in g["hooks"]]
    assert mine in starts and len(starts) == 2
    assert len(s["hooks"]["SubagentStart"]) == 1
    guard = s["hooks"]["PreToolUse"][0]
    assert guard["matcher"] == agents.CLAUDE_PIECES["web-guard"][1] and "--block" in guard["hooks"][0]["command"]
    assert "hook report-check" in s["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert all(st["state"] == "installed" for st in agents.claude_plan()["steps"])
    assert set(install.load_record()["agents"]["claude_code"]["pieces"]) == set(agents.CLAUDE_PIECES)


def test_block_option_updates_in_place():
    agents.claude_apply(block_web=False)
    assert "--block" not in _settings()["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    agents.claude_apply(block_web=True)
    guards = _settings()["hooks"]["PreToolUse"]
    assert len(guards) == 1 and "--block" in guards[0]["hooks"][0]["command"]


def test_remove_leaves_only_user_hooks():
    path = agents.claude_settings()
    path.parent.mkdir(parents=True)
    mine = {"type": "command", "command": "echo mine"}
    path.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [mine]}]}}))
    agents.claude_apply()
    out = agents.claude_remove()
    assert sorted(out["removed"]) == sorted(agents.CLAUDE_PIECES)
    assert _settings() == {"hooks": {"SessionStart": [{"hooks": [mine]}]}}
    assert "claude_code" not in install.load_record()["agents"]


def test_repair_fixes_approved_pieces_only(monkeypatch):
    agents.claude_apply(pieces={"session-start", "subagent-start"})
    monkeypatch.setattr(install, "hook_command", lambda name, *a: ["/new/python", "-m",
                                                                   "hyperroute_mcp", "hook", name, *a])
    fixed = agents.claude_repair()
    assert fixed == ["session-start", "subagent-start"]
    s = _settings()
    assert "PreToolUse" not in s.get("hooks", {})
    assert s["hooks"]["SessionStart"][0]["hooks"][0]["command"].startswith("/new/python")
    assert agents.claude_new_pieces() == ["report-check", "web-guard"]


def test_server_name_read_from_claude_json(sandbox):
    (sandbox / "claude").mkdir()
    (sandbox / "claude" / ".claude.json").write_text(json.dumps(
        {"mcpServers": {"router": {"command": "uvx", "args": ["hyperroute-mcp@latest"]}}}))
    assert agents.claude_server_name() == "router"
    assert "--server router" in agents.claude_plan()["steps"][0]["command"]


@pytest.mark.parametrize("payload,expected", [
    ({"tool_name": "WebSearch"}, True),
    ({"tool_name": "mcp__github__search_code"}, True),
    ({"tool_name": "mcp__hyperroute__execute"}, False),
    ({"tool_name": "Bash", "tool_input": {"command": "curl -s https://api.coingecko.com/x"}}, True),
    ({"tool_name": "Bash", "tool_input": {"command": "python3 -c 'import requests; requests.get(u)'"}}, True),
    ({"tool_name": "Bash", "tool_input": {"command": "curl -s http://localhost:8077/health"}}, False),
    ({"tool_name": "Bash", "tool_input": {"command": "pytest -q && git status"}}, False),
    ({"tool_name": "Read", "tool_input": {"file_path": "/x"}}, False),
])
def test_outside_calls(payload, expected):
    assert hooks.outside(payload, "hyperroute") is expected


@pytest.fixture
def logged(monkeypatch):
    monkeypatch.setattr(hooks, "logged_in", lambda: True)


def _deny(out):
    return bool(out) and out["hookSpecificOutput"].get("permissionDecision") == "deny"


def test_hook_rules_are_the_tested_wording():
    text = hooks.output("session-start", ["--server", "hr"])["hookSpecificOutput"]["additionalContext"]
    assert "CRITICAL FAILURE" in text and "mcp__hr__recommend" in text
    assert "one `recommend` covering two items of a list" in text


def test_a_handed_back_lookup_lets_one_call_for_that_lookup_through(logged):
    fetch = {"tool_name": "WebFetch", "tool_input": {"url": "https://pypi.org/pypi/httpx/json"}}
    assert _deny(hooks.output("web-guard", ["--block"], fetch))
    assert ledger.handed_back("session: s\nverdict: use_native  (use your own tools)\nact: x")
    ledger.grant_pass("latest released version of the Python package httpx")
    other = {"tool_name": "WebFetch", "tool_input": {"url": "https://registry.npmjs.org/react"}}
    assert _deny(hooks.output("web-guard", ["--block"], other))
    assert hooks.output("web-guard", ["--block"], fetch) is None
    assert _deny(hooks.output("web-guard", ["--block"], fetch))


def test_kind_lines_hand_back_too():
    assert ledger.handed_back("session: s\nkind:    NO_TOOL — nothing here\nact: x")
    assert not ledger.handed_back("session: s\nverdict: interpose\nact: execute(...)")


def test_guard_never_blocks_when_logged_out_or_hyperroute_is_down(monkeypatch, logged):
    search = {"tool_name": "WebSearch", "tool_input": {"query": "x"}}
    ledger.note_outage()
    assert hooks.output("web-guard", ["--block"], search) is None
    monkeypatch.setattr(ledger, "in_outage", lambda: False)
    monkeypatch.setattr(hooks, "logged_in", lambda: False)
    out = hooks.output("web-guard", ["--block"], search)
    assert not _deny(out) and "not logged in" in out["hookSpecificOutput"]["additionalContext"]


def test_guard_remind_only_by_env(monkeypatch, logged):
    monkeypatch.setenv("HYPERROUTE_GUARD", "remind")
    assert not _deny(hooks.output("web-guard", ["--block"], {"tool_name": "WebSearch"}))


def test_report_check_holds_the_turn_until_every_run_is_reported():
    assert hooks.output("report-check", []) is None
    ledger.add_run("nws_weather", "s-1")
    ledger.add_run("nws_weather", "s-1")
    first = hooks.output("report-check", [])
    assert first["decision"] == "block" and "nws_weather" in first["reason"]
    ledger.clear_run("nws_weather", "s-1")
    assert hooks.output("report-check", [])["decision"] == "block"
    ledger.clear_run("nws_weather", "s-1")
    assert hooks.output("report-check", []) is None


def test_report_check_lets_go_after_repeated_refusals():
    ledger.add_run("nominatim", "s-2")
    outs = [hooks.output("report-check", []) for _ in range(hooks.STOP_BLOCKS + 1)]
    assert all(o for o in outs[:-1]) and outs[-1] is None


def test_widened_piece_needs_fresh_consent(monkeypatch):
    agents.claude_apply()
    rec = install.load_record()
    rec["agents"]["claude_code"]["pieces"]["web-guard"]["rev"] = 1
    install.save_record(rec)
    assert agents.claude_new_pieces() == ["web-guard"]
    monkeypatch.setattr(install, "hook_command", lambda name, *a: ["/moved/python", "-m",
                                                                   "hyperroute_mcp", "hook", name, *a])
    assert agents.claude_repair() == ["report-check", "session-start", "subagent-start"]


def test_hooks_say_log_in_first_when_logged_out(monkeypatch):
    monkeypatch.setattr(hooks, "logged_in", lambda: False)
    s = hooks.output("session-start", [])["hookSpecificOutput"]["additionalContext"]
    assert s.startswith("HyperRoute is not logged in yet") and "mcp__hyperroute__use_token" in s
    monkeypatch.setattr(hooks, "logged_in", lambda: True)
    s = hooks.output("session-start", [])["hookSpecificOutput"]["additionalContext"]
    assert "not logged in" not in s


def test_the_pass_most_about_the_call_is_spent():
    ledger.grant_pass("httpx security advisories")
    ledger.grant_pass("httpx async and HTTP/2 support docs")
    assert ledger.take_pass("https://www.python-httpx.org/async/ http2 support")
    left = ledger.load()["passes"]
    assert len(left) == 1 and "advisories" in left[0]["words"]
