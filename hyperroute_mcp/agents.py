from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

from . import install

HOOK_MARK = re.compile(r"hyperroute[-_]mcp\S*\s+hook\s+(session-start|subagent-start|web-guard|report-check)\b")

CLAUDE_PIECES = {
    "session-start": ("SessionStart", None,
                      "Tell the main agent, at the start of every session, to route external tasks "
                      "through HyperRoute."),
    "subagent-start": ("SubagentStart", None,
                       "Tell every subagent the same thing the moment it starts."),
    "report-check": ("Stop", None,
                     "When the agent is about to finish while a tool HyperRoute ran has no report "
                     "yet, ask it to send the report first (at most five times in a row)."),
}
REVISIONS = {"session-start": 1, "subagent-start": 1, "report-check": 1}
RETIRED = {"web-guard": "PreToolUse"}


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


def _command(piece: str, server: str) -> str:
    return shlex.join(install.hook_command(piece, "--server", server))


def _event(piece: str) -> str:
    return CLAUDE_PIECES[piece][0] if piece in CLAUDE_PIECES else RETIRED[piece]


def _entries(settings: dict, event: str) -> list:
    hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    groups = hooks.get(event)
    return groups if isinstance(groups, list) else []


def _installed(settings: dict, pieces=CLAUDE_PIECES) -> dict:
    found = {}
    for piece in pieces:
        for group in _entries(settings, _event(piece)):
            for h in (group.get("hooks") or []) if isinstance(group, dict) else []:
                if isinstance(h, dict) and _is_ours(h) and f" hook {piece}" in h["command"]:
                    found[piece] = (group.get("matcher"), h["command"])
    return found


def _strip(settings: dict, pieces: set[str]) -> dict:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return settings
    for piece in pieces:
        event = _event(piece)
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


def claude_plan(cwd: str | None = None) -> dict:
    path = claude_settings()
    settings = _read_json(path)
    present = _installed(settings)
    server = claude_server_name(cwd)
    steps = []
    for piece, (event, matcher, desc) in CLAUDE_PIECES.items():
        want = _command(piece, server)
        if present.get(piece) == (matcher, want):
            state = "installed"
        elif piece in present:
            state = "update"
        else:
            state = "add"
        steps.append({"piece": piece, "state": state, "what": desc,
                      "file": str(path), "hook": event + (f" ({matcher})" if matcher else ""),
                      "command": want})
    return {"agent": "claude_code", "server": server, "file": str(path), "steps": steps}


def claude_apply(cwd: str | None = None, pieces: set[str] | None = None) -> dict:
    _drop_retired()
    plan = claude_plan(cwd)
    pieces = pieces or set(CLAUDE_PIECES)
    path = claude_settings()
    settings = _strip(_read_json(path), pieces)
    record = install.load_record()
    entry = record["agents"].setdefault("claude_code", {"pieces": {}})
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
    return claude_plan(cwd)


def claude_remove() -> dict:
    path = claude_settings()
    settings = _read_json(path)
    present = set(_installed(settings, [*CLAUDE_PIECES, *RETIRED]))
    if present:
        path.write_text(json.dumps(_strip(settings, set(CLAUDE_PIECES) | set(RETIRED)), indent=2) + "\n")
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
    retired = set(_installed(_read_json(claude_settings()), RETIRED)) | (set(entry["pieces"]) & set(RETIRED))
    plan = claude_plan()
    stale = {s["piece"] for s in plan["steps"] if s["piece"] in approved and s["state"] != "installed"}
    if not stale and not retired:
        return []
    if stale:
        claude_apply(pieces=stale)
    else:
        _drop_retired()
    return sorted(stale | retired)


def _drop_retired() -> None:
    path = claude_settings()
    settings = _read_json(path)
    if _installed(settings, RETIRED):
        path.write_text(json.dumps(_strip(settings, set(RETIRED)), indent=2) + "\n")
    record = install.load_record()
    entry = record["agents"].get("claude_code")
    if entry:
        for piece in RETIRED:
            (entry.get("pieces") or {}).pop(piece, None)
        entry.pop("options", None)
        install.save_record(record)


def claude_new_pieces() -> list[str]:
    have = (install.load_record()["agents"].get("claude_code") or {}).get("pieces") or {}
    return sorted(p for p in CLAUDE_PIECES
                  if p not in have or (have[p] or {}).get("rev", 1) < REVISIONS[p])


LABELS = {"codex": "Codex", "opencode": "OpenCode"}


def _codex_home() -> Path:
    p = os.environ.get("CODEX_HOME")
    return Path(p).expanduser() if p else Path.home() / ".codex"


def _codex_registered() -> bool:
    try:
        text = (_codex_home() / "config.toml").read_text()
    except Exception:
        return False
    return bool(re.search(r"^\[mcp_servers\.[^\]]+\][^\[]*hyperroute", text, re.M | re.S))


def _opencode_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".config"
    return root / "opencode" / "opencode.json"


def register(product: str, launch: list[str]) -> str | None:
    if product == "codex":
        if _codex_registered():
            return None
        codex = shutil.which("codex")
        cmd = ["codex", "mcp", "add", "hyperroute", "--", *launch]
        if not codex:
            return "Register HyperRoute with Codex by running:\n  " + " ".join(cmd)
        r = subprocess.run([codex, *cmd[1:]], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return "Could not register HyperRoute with Codex. Run:\n  " + " ".join(cmd)
        return None
    if product == "opencode":
        path = _opencode_file()
        data = _read_json(path) if path.exists() else {}
        if path.exists() and not data:
            try:
                if path.read_text().strip():
                    return f"Could not read {path}; add an MCP server named hyperroute that runs `{' '.join(launch)}`."
            except Exception:
                pass
        data.setdefault("$schema", "https://opencode.ai/config.json")
        mcp = data.setdefault("mcp", {})
        mcp["hyperroute"] = {"type": "local", "command": list(launch), "enabled": True}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n")
        return None
    return f"No installer for {product}."


def unregister(product: str) -> bool:
    if product == "codex":
        codex = shutil.which("codex")
        if not codex or not _codex_registered():
            return False
        return subprocess.run([codex, "mcp", "remove", "hyperroute"], capture_output=True, timeout=60).returncode == 0
    if product == "opencode":
        path = _opencode_file()
        data = _read_json(path)
        if not (data.get("mcp") or {}).pop("hyperroute", None):
            return False
        if not data["mcp"]:
            data.pop("mcp")
        path.write_text(json.dumps(data, indent=2) + "\n")
        return True
    return False


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
