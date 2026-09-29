import json

import pytest

from hyperroute_mcp import agents, hooks, install


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


def test_hook_outputs():
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
    guard = s["hooks"]["PreToolUse"][0]
    assert guard["matcher"] == agents.CLAUDE_PIECES["web-guard"][1] and "--block" not in guard["hooks"][0]["command"]
    assert all(st["state"] == "installed" for st in agents.claude_plan()["steps"])
    assert set(install.load_record()["agents"]["claude_code"]["pieces"]) == set(agents.CLAUDE_PIECES)


def test_block_option_updates_in_place():
    agents.claude_apply()
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
    assert agents.claude_new_pieces() == ["web-guard"]


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


def test_self_verdict_clears_the_guard():
    blocked = hooks.output("web-guard", ["--block"], {"tool_name": "WebSearch"})
    assert blocked["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert install.note_verdict("session: s\nverdict: use_native  (use your own tools)\nact: x") == "use_native"
    assert hooks.output("web-guard", ["--block"], {"tool_name": "WebSearch"}) is None
    assert hooks.output("web-guard", [], {"tool_name": "Bash",
                                          "tool_input": {"command": "curl https://x.io"}}) is None


def test_kind_lines_clear_too():
    assert install.note_verdict("session: s\nkind:    NO_TOOL — nothing here\nact: x") == "NO_TOOL"
    assert install.note_verdict("session: s\nverdict: interpose\nact: execute(...)") is None


def test_widened_piece_needs_fresh_consent(monkeypatch):
    agents.claude_apply()
    rec = install.load_record()
    rec["agents"]["claude_code"]["pieces"]["web-guard"]["rev"] = 1
    install.save_record(rec)
    assert agents.claude_new_pieces() == ["web-guard"]
    monkeypatch.setattr(install, "hook_command", lambda name, *a: ["/moved/python", "-m",
                                                                   "hyperroute_mcp", "hook", name, *a])
    assert agents.claude_repair() == ["session-start", "subagent-start"]


def test_hooks_say_log_in_first_when_logged_out(monkeypatch):
    monkeypatch.setattr(hooks, "logged_in", lambda: False)
    s = hooks.output("session-start", [])["hookSpecificOutput"]["additionalContext"]
    assert s.startswith("HyperRoute is not logged in yet") and "mcp__hyperroute__login_link" in s
    monkeypatch.setattr(hooks, "logged_in", lambda: True)
    s = hooks.output("session-start", [])["hookSpecificOutput"]["additionalContext"]
    assert "not logged in" not in s
