import json

import pytest

from hyperroute_mcp import agents, cli


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("HYPERROUTE_HOME", str(tmp_path / "hr"))
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_no_args_runs_the_server():
    assert cli.main([]) is None


def test_unknown_command_prints_help(capsys):
    assert cli.main(["instal"]) == 2
    err = capsys.readouterr().err
    assert "unknown command: instal" in err and "hyperroute-mcp install" in err


def test_install_without_agent_explains(capsys):
    assert cli.main(["install"]) == 1
    assert "--agent claude_code" in capsys.readouterr().out


def test_install_in_claude_code_asks_before_hooks(monkeypatch, capsys):
    monkeypatch.setenv("CLAUDECODE", "1")
    assert cli.main(["install"]) == 0
    out = capsys.readouterr().out
    assert "claude mcp add --scope user hyperroute --" in out
    assert "Yes: type  ! " in out and "install --apply" in out and len(out.splitlines()) <= 6
    assert not agents.claude_settings().exists()


def test_install_apply_then_remove(monkeypatch, capsys):
    monkeypatch.setenv("CLAUDECODE", "1")
    assert cli.main(["install", "--apply"]) == 0
    hooks = json.loads(agents.claude_settings().read_text())["hooks"]
    assert set(hooks) == {"SessionStart", "SubagentStart", "PreToolUse"}
    capsys.readouterr()
    assert cli.main(["install"]) == 0
    assert "Restart Claude Code if" in capsys.readouterr().out
    assert cli.main(["install", "--remove"]) == 0
    assert json.loads(agents.claude_settings().read_text()) == {}


def test_already_registered_is_not_re_added(monkeypatch, sandbox, capsys):
    monkeypatch.setenv("CLAUDECODE", "1")
    (sandbox / "claude").mkdir()
    (sandbox / "claude" / ".claude.json").write_text(json.dumps(
        {"mcpServers": {"hr": {"command": "uvx", "args": ["hyperroute-mcp@latest"]}}}))
    cli.main(["install"])
    assert "HyperRoute is already in Claude Code." in capsys.readouterr().out
