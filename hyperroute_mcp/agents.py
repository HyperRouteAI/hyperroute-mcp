from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from pathlib import Path

from . import install

HOOK_MARK = re.compile(r"hyperroute[-_]mcp\S*\s+hook\s+(session-start|subagent-start|web-guard)\b")

CLAUDE_PIECES = {
    "session-start": ("SessionStart", None,
                      "Tell the main agent, at the start of every session, to route external tasks "
                      "through HyperRoute."),
    "subagent-start": ("SubagentStart", None,
                       "Tell every subagent the same thing the moment it starts."),
    "web-guard": ("PreToolUse", "WebSearch|WebFetch|Bash|mcp__.*",
                  "When the agent reaches outside the machine on its own (web search or fetch, a "
                  "network command in the shell, another MCP server), remind it to route through "
                  "HyperRoute. Local work is untouched, and after HyperRoute says to use its own "
                  "tools it stays quiet for 10 minutes."),
}
REVISIONS = {"session-start": 1, "subagent-start": 1, "web-guard": 2}


def _fingerprint(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def claude_dir() -> Path:
    p = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(p).expanduser() if p else Path.home() / ".claude"


def claude_settings() -> Path:
    return claude_dir() / "settings.json"


def _claude_json() -> Path:
    p = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(p).expanduser() / ".claude.json" if p else Path.home() / ".claude.json"


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _is_ours(entry: dict) -> bool:
    cmd = entry.get("command")
    if not isinstance(cmd, str):
        return False
    return bool(HOOK_MARK.search(cmd)) or ("hyperroute_mcp hook " in cmd)


def _servers(block: dict) -> dict:
    s = block.get("mcpServers") if isinstance(block, dict) else None
    return s if isinstance(s, dict) else {}


def _hyperroute_in(servers: dict) -> str | None:
    for name, spec in servers.items():
        if not isinstance(spec, dict):
            continue
        blob = " ".join([str(spec.get("command", "")), *map(str, spec.get("args") or []),
                         str(spec.get("url", ""))])
        if "hyperroute" in blob.lower():
            return name
    return None


def claude_user_server() -> str | None:
    return _hyperroute_in(_servers(_read_json(_claude_json())))


def claude_server_name(cwd: str | None = None) -> str:
    data = _read_json(_claude_json())
    blocks = [_servers(data)]
    projects = data.get("projects") if isinstance(data.get("projects"), dict) else {}
    if cwd and cwd in projects:
        blocks.insert(0, _servers(projects[cwd]))
    blocks += [_servers(p) for p in projects.values() if isinstance(p, dict)]
    if cwd:
        blocks.insert(0, _servers(_read_json(Path(cwd) / ".mcp.json")))
    for servers in blocks:
        name = _hyperroute_in(servers)
        if name:
            return name
    return "hyperroute"


def _command(piece: str, server: str, block: bool) -> str:
    args = ["--server", server]
    if piece == "web-guard" and block:
        args.append("--block")
    return shlex.join(install.hook_command(piece, *args))


def _entries(settings: dict, event: str) -> list:
    hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    groups = hooks.get(event)
    return groups if isinstance(groups, list) else []


def _installed(settings: dict) -> dict:
    found = {}
    for piece, (event, _m, _d) in CLAUDE_PIECES.items():
        for group in _entries(settings, event):
            for h in (group.get("hooks") or []) if isinstance(group, dict) else []:
                if isinstance(h, dict) and _is_ours(h) and f" hook {piece}" in h["command"]:
                    found[piece] = (group.get("matcher"), h["command"])
    return found


def _strip(settings: dict, pieces: set[str]) -> dict:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return settings
    for piece in pieces:
        event = CLAUDE_PIECES[piece][0]
        kept = []
        for group in hooks.get(event) or []:
            if not isinstance(group, dict):
                kept.append(group)
                continue
            inner = [h for h in group.get("hooks") or []
                     if not (isinstance(h, dict) and _is_ours(h) and f" hook {piece}" in h["command"])]
            if inner:
                kept.append({**group, "hooks": inner})
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    if not hooks:
        settings.pop("hooks", None)
    return settings


def _add(settings: dict, piece: str, command: str) -> dict:
    event, matcher, _d = CLAUDE_PIECES[piece]
    hooks = settings.setdefault("hooks", {})
    group = {"hooks": [{"type": "command", "command": command, "timeout": 10}]}
    if matcher:
        group = {"matcher": matcher, **group}
    hooks.setdefault(event, []).append(group)
    return settings


def claude_plan(block_web: bool = False, cwd: str | None = None) -> dict:
    path = claude_settings()
    settings = _read_json(path)
    present = _installed(settings)
    server = claude_server_name(cwd)
    steps = []
    for piece, (event, matcher, desc) in CLAUDE_PIECES.items():
        want = _command(piece, server, block_web)
        if present.get(piece) == (matcher, want):
            state = "installed"
        elif piece in present:
            state = "update"
        else:
            state = "add"
        d = desc
        if piece == "web-guard" and block_web:
            d = ("Block the agent from reaching outside the machine on its own (web search or fetch, a "
                 "network command in the shell, another MCP server), telling it to route through "
                 "HyperRoute. Local work is untouched, and after HyperRoute says to use its own "
                 "tools it is let through for 10 minutes.")
        steps.append({"piece": piece, "state": state, "what": d,
                      "file": str(path), "hook": event + (f" ({matcher})" if matcher else ""),
                      "command": want})
    return {"agent": "claude_code", "server": server, "file": str(path), "steps": steps}


def claude_apply(block_web: bool = False, cwd: str | None = None,
                 pieces: set[str] | None = None) -> dict:
    plan = claude_plan(block_web, cwd)
    pieces = pieces or set(CLAUDE_PIECES)
    path = claude_settings()
    settings = _read_json(path)
    settings = _strip(settings, pieces)
    record = install.load_record()
    entry = record["agents"].setdefault("claude_code", {"pieces": {}})
    entry["options"] = {"block_web": block_web}
    for step in plan["steps"]:
        if step["piece"] not in pieces:
            continue
        _add(settings, step["piece"], step["command"])
        entry["pieces"][step["piece"]] = {
            "file": str(path), "version": install.version(), "rev": REVISIONS[step["piece"]],
            "fingerprint": _fingerprint(step["command"])}
    entry["server"] = plan["server"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n")
    install.save_record(record)
    return claude_plan(block_web, cwd)


def claude_remove() -> dict:
    path = claude_settings()
    settings = _read_json(path)
    present = set(_installed(settings))
    if present:
        path.write_text(json.dumps(_strip(settings, set(CLAUDE_PIECES)), indent=2) + "\n")
    record = install.load_record()
    record["agents"].pop("claude_code", None)
    install.save_record(record)
    return {"agent": "claude_code", "removed": sorted(present), "file": str(path)}


def claude_repair() -> list[str]:
    record = install.load_record()
    entry = record["agents"].get("claude_code")
    if not entry or not entry.get("pieces"):
        return []
    approved = {p for p, v in entry["pieces"].items()
                if p in CLAUDE_PIECES and (v or {}).get("rev", 1) == REVISIONS[p]}
    block = bool((entry.get("options") or {}).get("block_web"))
    plan = claude_plan(block)
    stale = {s["piece"] for s in plan["steps"] if s["piece"] in approved and s["state"] != "installed"}
    if not stale:
        return []
    claude_apply(block, pieces=stale)
    return sorted(stale)


def claude_new_pieces() -> list[str]:
    have = (install.load_record()["agents"].get("claude_code") or {}).get("pieces") or {}
    return sorted(p for p in CLAUDE_PIECES
                  if p not in have or (have[p] or {}).get("rev", 1) < REVISIONS[p])


OPTIONS = {
    "claude_code": {"plan": claude_plan, "apply": claude_apply, "remove": claude_remove,
                    "repair": claude_repair, "new_pieces": claude_new_pieces},
}


def supported(product: str | None) -> bool:
    return product in OPTIONS


def repair_all() -> dict:
    out = {}
    for product in install.load_record()["agents"]:
        opt = OPTIONS.get(product)
        if opt:
            try:
                fixed = opt["repair"]()
                if fixed:
                    out[product] = fixed
            except Exception:
                pass
    return out
