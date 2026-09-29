from __future__ import annotations

import os
import shutil
import subprocess
import sys

from . import agents, hooks, install

HELP = """hyperroute-mcp — HyperRoute for coding agents.

  hyperroute-mcp install            set up HyperRoute for the agent running this command
  hyperroute-mcp install --details  also list exactly what it changed
  hyperroute-mcp install --remove   undo the setup
  hyperroute-mcp --version          print the version

Run with no arguments, it is the MCP server your agent launches.
"""


def _self(*args: str) -> str:
    if install.method() == "uvx":
        return " ".join(["uvx", f"{install.PACKAGE}@latest", *args])
    if shutil.which(install.PACKAGE):
        return " ".join([install.PACKAGE, *args])
    return " ".join([sys.executable, "-m", "hyperroute_mcp", *args])


def _launch() -> list[str]:
    if install.method() == "uvx":
        return [shutil.which("uvx") or "uvx", f"{install.PACKAGE}@latest"]
    exe = shutil.which(install.PACKAGE)
    if exe:
        return [exe]
    return [sys.executable, "-m", "hyperroute_mcp"]


def _agent(argv: list[str]) -> str | None:
    if "--agent" in argv:
        i = argv.index("--agent")
        if i + 1 < len(argv):
            return argv[i + 1]
    if os.environ.get("CLAUDECODE") == "1" or os.environ.get("CLAUDE_CODE_ENTRYPOINT"):
        return "claude_code"
    return None


def _register_claude() -> str:
    if agents.claude_user_server():
        return "HyperRoute is already in Claude Code."
    claude = shutil.which("claude")
    cmd = ["claude", "mcp", "add", "--scope", "user", "hyperroute", "--", *_launch()]
    if not claude:
        return "Register HyperRoute with Claude Code by running:\n  " + " ".join(cmd)
    r = subprocess.run([claude, *cmd[1:]], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return ("Could not register HyperRoute with Claude Code automatically. Run:\n  "
                + " ".join(cmd) + "\n" + (r.stderr or r.stdout).strip()[-400:])
    return "HyperRoute is added to Claude Code."


def _claude_install(argv: list[str]) -> int:
    block = "--block-web" in argv
    if "--remove" in argv:
        out = agents.claude_remove()
        print("HyperRoute's hooks are removed." if out["removed"] else "Nothing to remove.")
        return 0
    registered = _register_claude()
    plan = agents.claude_apply(block_web=block, cwd=os.getcwd())
    if "--details" in argv:
        for s in plan["steps"]:
            print(f"  {s['what']} ({s['hook']} in {s['file']})")
    if not registered.startswith("HyperRoute is"):
        print(registered)
    print("HyperRoute is set up for Claude Code and its subagents.")
    if hooks.logged_in():
        print("Restart Claude Code to start using it.")
    else:
        print("Restart Claude Code, then paste your login line from hyperroute.io (Connect, step 2).")
    return 0


def install_command(argv: list[str]) -> int:
    agent = _agent(argv)
    if agent == "claude_code":
        return _claude_install(argv)
    print("HyperRoute could not tell which agent is running this command.")
    print("Claude Code: rerun as `" + _self("install", "--agent", "claude_code") + "`.")
    print("Other agents: add an MCP server named hyperroute that runs `"
          + " ".join(_launch()) + "`, then ask it to run HyperRoute's `setup`.")
    return 1


def main(argv: list[str]) -> int | None:
    if not argv:
        return None
    head = argv[0]
    if head == "hook":
        from .hooks import main as hook_main
        return hook_main(argv[1:])
    if head in ("--version", "version"):
        print(install.version())
        return 0
    if head in ("-h", "--help", "help"):
        print(HELP)
        return 0
    if head == "install":
        return install_command(argv[1:])
    sys.stderr.write(f"unknown command: {head}\n\n{HELP}")
    return 2
