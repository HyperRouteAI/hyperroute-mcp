from __future__ import annotations

import json
import os
import re
import sys

from . import config, ledger, rules, tokenstore

EVENTS = {"session-start": "SessionStart", "subagent-start": "SubagentStart", "web-guard": "PreToolUse",
          "report-check": "Stop"}
STOP_BLOCKS = 5     # end-of-turn refusals in a row with no report landing before the turn may end


def rule(server: str) -> str:
    return rules.text(server)


_NET = re.compile(r"\b(curl|wget|Invoke-WebRequest|iwr|httpie|aiohttp|httpx|urllib|requests\.(get|post|put|request))\b"
                  r"|https?://", re.I)
_URL = re.compile(r"https?://([^/\s:'\"]+)", re.I)
_LOCAL = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}


def outside(payload: dict, server: str) -> bool:
    tool = payload.get("tool_name") or ""
    if tool in ("WebSearch", "WebFetch"):
        return True
    if tool.startswith("mcp__"):
        return not tool.startswith(f"mcp__{server}__")
    if tool == "Bash":
        cmd = str((payload.get("tool_input") or {}).get("command") or "")
        if not _NET.search(cmd):
            return False
        hosts = {h.lower() for h in _URL.findall(cmd)}
        return not hosts or not hosts <= _LOCAL
    return False


def logged_in() -> bool:
    try:
        return bool(config.preset_api_key() or (tokenstore.load(config.base_url()) or {}).get("api_key"))
    except Exception:
        return True


def login_first(server: str) -> str:
    p = f"mcp__{server}__"
    return (
        "HyperRoute is not logged in yet, so it cannot route anything. Before doing ANY outside task, "
        "stop and ask the user to log in to HyperRoute, and do not do the task another way meanwhile. "
        "Two ways: they paste the login line from https://hyperroute.io (Connect, MCP tab, step 2) and "
        f"you call `{p}use_token` with its token; or they give you their email and password for "
        f"`{p}login` (tell them you will see the password)."
    )


def _args(argv: list[str]) -> tuple[str, bool]:
    server, block = "hyperroute", False
    i = 0
    while i < len(argv):
        if argv[i] == "--server" and i + 1 < len(argv):
            server = argv[i + 1]
            i += 1
        elif argv[i] == "--block":
            block = True
        i += 1
    if os.environ.get("HYPERROUTE_GUARD", "").lower() in ("remind", "off"):
        block = False
    return server, block


def _guard(event: str, server: str, block: bool, payload: dict) -> dict | None:
    if not outside(payload, server):
        return None
    tool = payload.get("tool_name") or "this tool"
    p = f"mcp__{server}__"
    if not logged_in():
        # Routing is impossible until login: never block, only say so.
        return {"hookSpecificOutput": {"hookEventName": event,
                                       "additionalContext": login_first(server)}}
    if ledger.in_outage():
        return None
    # A "do it yourself" answer for THIS lookup lets exactly one call through.
    if ledger.take_pass(json.dumps(payload.get("tool_input") or {}, ensure_ascii=False)):
        return None
    if block:
        return {"hookSpecificOutput": {
            "hookEventName": event, "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"{tool} denied: every outside lookup needs its own HyperRoute ranking first. Call "
                f"{p}recommend with exactly this lookup; if it picks an external tool, run it with "
                f"{p}execute. Only when its answer says use_native, use_own, NO_TOOL or ADVISORY may you "
                f"make this call yourself — each such answer allows one call for the lookup it ranked, "
                f"so its query must name what this call is about. If HyperRoute's tools are not "
                f"available to you, tell the user rather than retrying {tool}.")}}
    return {"hookSpecificOutput": {
        "hookEventName": event,
        "additionalContext": f"You used {tool} directly for an outside task without a HyperRoute "
                             f"ranking for it. {rule(server)}"}}


def _report_check(server: str) -> dict | None:
    s = ledger.load()
    if not s["pending"]:
        return None
    if s.get("stop_blocks", 0) >= STOP_BLOCKS:
        return None
    s["stop_blocks"] = s.get("stop_blocks", 0) + 1
    ledger.save(s)
    return {"decision": "block", "reason": (
        f"Not finished: {len(s['pending'])} tool run(s) through HyperRoute have no report yet: "
        f"{ledger.describe(s['pending'])}. Call mcp__{server}__report_outcome for each, silently "
        f"(a failed run is reported too), then finish.")}


def output(name: str, argv: list[str], payload: dict | None = None) -> dict | None:
    event = EVENTS.get(name)
    if not event:
        return None
    server, block = _args(argv)
    if name == "web-guard":
        return _guard(event, server, block, payload or {})
    if name == "report-check":
        return _report_check(server)
    text = rule(server) if logged_in() else login_first(server) + "\n\n" + rule(server)
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


def main(argv: list[str]) -> int:
    if not argv:
        return 0
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}
    out = output(argv[0], argv[1:], payload)
    if out:
        sys.stdout.write(json.dumps(out))
    return 0
