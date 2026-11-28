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
    assert "mcp__hr__run" in s["hookSpecificOutput"]["additionalContext"]
    sub = hooks.output("subagent-start", [])
    assert sub["hookSpecificOutput"]["hookEventName"] == "SubagentStart"
    assert hooks.output("nope", []) is None


def test_plan_changes_nothing():
    plan = agents.claude_plan()
    assert [s["state"] for s in plan["steps"]] == ["add", "add", "add"]
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
    assert "PreToolUse" not in s["hooks"]
    assert "hook report-check" in s["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert all(st["state"] == "installed" for st in agents.claude_plan()["steps"])
    assert set(install.load_record()["agents"]["claude_code"]["pieces"]) == set(agents.CLAUDE_PIECES)


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
    assert "Stop" not in s.get("hooks", {})
    assert s["hooks"]["SessionStart"][0]["hooks"][0]["command"].startswith("/new/python")
    assert agents.claude_new_pieces() == ["report-check"]


def test_server_name_read_from_claude_json(sandbox):
    (sandbox / "claude").mkdir()
    (sandbox / "claude" / ".claude.json").write_text(json.dumps(
        {"mcpServers": {"router": {"command": "uvx", "args": ["hyperroute-mcp@latest"]}}}))
    assert agents.claude_server_name() == "router"
    assert "--server router" in agents.claude_plan()["steps"][0]["command"]


def test_hook_rules_are_the_tested_wording():
    text = hooks.output("session-start", ["--server", "hr"])["hookSpecificOutput"]["additionalContext"]
    assert "CRITICAL FAILURE" in text and "mcp__hr__run" in text
    assert "Never two lookups in one item" in text and "recommend" not in text


def test_report_check_holds_the_turn_until_every_run_is_reported():
    assert hooks.output("report-check", []) is None
    ledger.add_run("nws_weather", "r_1/1")
    ledger.add_run("nws_weather", "r_1/2")
    first = hooks.output("report-check", [])
    assert first["decision"] == "block" and "r_1/1 (nws_weather)" in first["reason"]
    assert "report_outcome(item=" in first["reason"]
    ledger.clear_item("r_1/1")
    assert hooks.output("report-check", [])["decision"] == "block"
    ledger.clear_item("r_1/2")
    assert hooks.output("report-check", []) is None


def test_report_check_lets_go_after_repeated_refusals():
    ledger.add_run("nominatim", "r_2/1")
    outs = [hooks.output("report-check", []) for _ in range(hooks.STOP_BLOCKS + 1)]
    assert all(o for o in outs[:-1]) and outs[-1] is None


def test_widened_piece_needs_fresh_consent(monkeypatch):
    agents.claude_apply()
    monkeypatch.setitem(agents.REVISIONS, "report-check", 2)
    assert agents.claude_new_pieces() == ["report-check"]
    monkeypatch.setattr(install, "hook_command", lambda name, *a: ["/moved/python", "-m",
                                                                   "hyperroute_mcp", "hook", name, *a])
    assert agents.claude_repair() == ["session-start", "subagent-start"]


def test_hooks_say_log_in_first_when_logged_out(monkeypatch):
    monkeypatch.setattr(hooks, "logged_in", lambda: False)
    s = hooks.output("session-start", [])["hookSpecificOutput"]["additionalContext"]
    assert s.startswith("HyperRoute is not logged in yet") and "mcp__hyperroute__use_token" in s
    monkeypatch.setattr(hooks, "logged_in", lambda: True)
    s = hooks.output("session-start", [])["hookSpecificOutput"]["additionalContext"]
    assert "not logged in" not in s


_OLD_GUARD = ["/old/python", "-m", "hyperroute_mcp", "hook", "web-guard", "--server", "hyperroute", "--block"]


def _with_old_guard():
    agents.claude_apply()
    path = agents.claude_settings()
    s = _settings()
    s["hooks"]["PreToolUse"] = [{"matcher": "WebSearch|WebFetch|Bash|mcp__.*", "hooks": [
        {"type": "command", "command": " ".join(_OLD_GUARD), "timeout": 10}]},
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}]
    path.write_text(json.dumps(s))
    rec = install.load_record()
    rec["agents"]["claude_code"]["pieces"]["web-guard"] = {"file": str(path), "rev": 2, "fingerprint": "x"}
    rec["agents"]["claude_code"]["options"] = {"block_web": True}
    install.save_record(rec)


def test_a_fresh_install_has_no_guard_hook():
    agents.claude_apply()
    assert "PreToolUse" not in _settings()["hooks"]
    assert "web-guard" not in install.load_record()["agents"]["claude_code"]["pieces"]


def test_an_old_guard_hook_is_dropped_at_start():
    _with_old_guard()
    assert agents.repair_all() == {"claude_code": ["web-guard"]}
    s = _settings()
    assert s["hooks"]["PreToolUse"] == [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}]
    assert set(s["hooks"]) == {"SessionStart", "SubagentStart", "PreToolUse", "Stop"}
    entry = install.load_record()["agents"]["claude_code"]
    assert set(entry["pieces"]) == set(agents.CLAUDE_PIECES) and "options" not in entry
    assert agents.claude_repair() == []


def test_remove_drops_an_old_guard_hook():
    _with_old_guard()
    out = agents.claude_remove()
    assert "web-guard" in out["removed"]
    assert _settings() == {"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}]}}


def test_an_old_guard_hook_call_does_nothing():
    assert hooks.output("web-guard", ["--block"], {"tool_name": "WebSearch"}) is None
