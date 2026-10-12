from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

from . import install

# One agent session's routing state, shared by the MCP server and the hooks it installed: the runs
# that still need a report, the one-use passes HyperRoute's "do it yourself" answers grant, and an
# outage window. Keyed by the agent's own process, which is an ancestor of both the MCP server and
# every hook, so two sessions on one machine never see each other's state.

PASS_SECONDS = 300
OUTAGE_SECONDS = 600
STALE_SECONDS = 86400
_AGENTS = ("claude", "codex", "opencode")
_HANDED_BACK = (re.compile(r"^verdict:\s*(use_native|use_own)\b", re.M),
                re.compile(r"^kind:\s*(NO_TOOL|ADVISORY)\b", re.M))
_NOT_RUN = {"needs_onboard", "use_native", "route_to_local", "report_first", "not_logged_in"}
_STOP = {"https", "http", "json", "html", "query", "latest", "search", "find", "what", "this", "that",
         "with", "from", "week", "current", "today", "fields", "filter", "select", "format", "give",
         "which", "your", "about", "info", "data", "page", "site", "status", "version", "results"}


def process(pid: int) -> tuple[int, str] | None:
    try:
        with open(f"/proc/{pid}/stat") as f:
            ppid = int(f.read().rsplit(")", 1)[1].split()[1])
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmd = f.read().replace(b"\0", b" ").decode(errors="replace")
        return ppid, cmd
    except Exception:
        pass
    try:
        out = subprocess.run(["ps", "-o", "ppid=", "-o", "command=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=3).stdout.strip()
        ppid, _, cmd = out.partition(" ")
        return int(ppid), cmd
    except Exception:
        return None


def agent_key() -> str:
    """The pid of the agent process this one runs under, or 'default' when none is found."""
    pid = os.getppid()
    for _ in range(12):
        got = process(pid)
        if not got:
            break
        ppid, cmd = got
        if "/@anthropic-ai/claude-code/" in cmd:
            return str(pid)
        for word in cmd.split()[:2]:
            base = os.path.basename(word).lower()
            if any(base == a or base.startswith(a + "-") or base.startswith(a + ".") for a in _AGENTS):
                return str(pid)
        if ppid <= 1:
            break
        pid = ppid
    return "default"


def _path(key: str | None = None) -> Path:
    return install.home() / "state" / f"{key or agent_key()}.json"


def load(key: str | None = None) -> dict:
    s = install._read(_path(key))
    s.setdefault("passes", [])
    s.setdefault("pending", [])
    return s


def save(s: dict, key: str | None = None) -> None:
    p = _path(key)
    install._write(p, s)
    try:
        for old in p.parent.glob("*.json"):
            if time.time() - old.stat().st_mtime > STALE_SECONDS:
                old.unlink()
    except Exception:
        pass


def words(text) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{4,}", str(text).lower()) if w not in _STOP}


def handed_back(text: str) -> bool:
    return isinstance(text, str) and any(rx.search(text) for rx in _HANDED_BACK)


# ---- passes: a "do it yourself" answer lets ONE outside call through, for the lookup it ranked

def grant_pass(query: str) -> None:
    s = load()
    s["passes"].append({"until": time.time() + PASS_SECONDS, "words": sorted(words(query))})
    save(s)


def take_pass(call_text: str) -> bool:
    """Spend a live pass whose lookup shares a distinctive word with this call."""
    s = load()
    now = time.time()
    s["passes"] = [p for p in s["passes"] if p.get("until", 0) > now]
    w = words(call_text)
    best = max(s["passes"], key=lambda p: len(w & set(p.get("words") or [])), default=None)
    if best is not None and w & set(best.get("words") or []):
        # The pass most about this call is the one spent, so a pass granted for one lookup is not
        # used up by another that merely shares a word with it.
        s["passes"].remove(best)
        save(s)
        return True
    save(s)
    return False


def note_outage() -> None:
    s = load()
    s["outage_until"] = time.time() + OUTAGE_SECONDS
    save(s)


def in_outage() -> bool:
    return time.time() < load().get("outage_until", 0)


# ---- runs that still owe a report

def ran(out) -> bool:
    """Whether an execute answer is a run of the tool (and so owes a report)."""
    if not isinstance(out, dict):
        return True
    return out.get("error") not in _NOT_RUN and not out.get("login_required")


def add_run(tool_id: str, session_id: str | None) -> None:
    s = load()
    s["pending"].append({"tool_id": tool_id, "session_id": session_id, "at": time.time()})
    s["stop_blocks"] = 0
    save(s)


def clear_run(tool_id: str, session_id: str | None) -> None:
    s = load()
    for match in (lambda r: r["tool_id"] == tool_id and r.get("session_id") == session_id,
                  lambda r: r["tool_id"] == tool_id):
        hit = next((r for r in s["pending"] if match(r)), None)
        if hit:
            s["pending"].remove(hit)
            s["stop_blocks"] = 0
            break
    save(s)


def pending(key: str | None = None) -> list[dict]:
    return load(key)["pending"]


def describe(runs: list[dict]) -> str:
    return ", ".join(f"{r['tool_id']} (session {r.get('session_id') or '?'})" for r in runs)
