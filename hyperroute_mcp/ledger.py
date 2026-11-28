from __future__ import annotations

import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:            # Windows: writes stay atomic, edits are not serialised
    fcntl = None

from . import install

# One agent session's routing state, shared by the MCP server and the hooks it installed: the runs
# that still need a report. Keyed by the agent's own process, which is an ancestor of both the MCP
# server and every hook, so two sessions on one machine never see each other's state.

STALE_SECONDS = 86400
_AGENTS = ("claude", "codex", "opencode")


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
    s.setdefault("pending", [])
    return s


def save(s: dict, key: str | None = None) -> None:
    p = _path(key)
    install._write(p, s)
    try:
        for old in [*p.parent.glob("*.json"), *p.parent.glob("*.lock")]:
            if time.time() - old.stat().st_mtime > STALE_SECONDS:
                old.unlink()
    except Exception:
        pass


@contextmanager
def edit(key: str | None = None):
    """Load, change and save the state under a lock. The MCP server and its hooks run at once;
    unlocked, one's save drops another's change."""
    p = _path(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p.with_suffix(".lock"), "a") as lock:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX)
        s = load(key)
        yield s
        save(s, key)


def add_run(tool_id: str, item: str, high: bool = False) -> None:
    with edit() as s:
        s["pending"].append({"tool_id": tool_id, "item": item, "high": high, "at": time.time()})
        s["stop_blocks"] = 0


def clear_item(item: str) -> None:
    with edit() as s:
        s["pending"] = [r for r in s["pending"] if r.get("item") != item]
        s["stop_blocks"] = 0


def pending(key: str | None = None) -> list[dict]:
    now = time.time()
    return [r for r in load(key)["pending"] if now - r.get("at", now) < STALE_SECONDS]


def describe(runs: list[dict]) -> str:
    return ", ".join(f"{r.get('item')} ({r.get('tool_id')})" for r in runs)

