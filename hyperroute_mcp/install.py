from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
import urllib.request
from pathlib import Path

PACKAGE = "hyperroute-mcp"
PYPI_URL = f"https://pypi.org/pypi/{PACKAGE}/json"
REQUEST_HEADER = "x-hyperroute-mcp"
RESPONSE_HEADER = "x-hyperroute-mcp-versions"
_DAY = 86400

_router: dict = {"latest": None, "min": None}


def version() -> str:
    try:
        from importlib.metadata import version as v
        return v(PACKAGE)
    except Exception:
        from . import __version__
        return __version__


def parse(v: str | None) -> tuple[int, ...] | None:
    if not v:
        return None
    m = re.match(r"^\s*(\d+(?:\.\d+)*)", v)
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def older(a: str | None, b: str | None) -> bool:
    pa, pb = parse(a), parse(b)
    return pa is not None and pb is not None and pa < pb


def home() -> Path:
    p = os.environ.get("HYPERROUTE_HOME")
    return Path(p).expanduser() if p else Path.home() / ".hyperroute"


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def record_path() -> Path:
    return home() / "integration.json"


def load_record() -> dict:
    r = _read(record_path())
    r.setdefault("agents", {})
    return r


def save_record(r: dict) -> None:
    _write(record_path(), r)


def note_router(header: str | None) -> None:
    if not header:
        return
    for part in header.split(";"):
        k, _, v = part.strip().partition("=")
        if k in ("latest", "min") and v:
            _router[k] = v.strip()


CLEARANCE_SECONDS = 600
_SELF_VERDICTS = (re.compile(r"^verdict:\s*(use_native|use_own)\b", re.M),
                  re.compile(r"^kind:\s*(NO_TOOL|ADVISORY)\b", re.M))


def clearance_path() -> Path:
    return home() / "clearance.json"


def note_verdict(text: str) -> str | None:
    if not isinstance(text, str):
        return None
    for rx in _SELF_VERDICTS:
        m = rx.search(text)
        if m:
            try:
                _write(clearance_path(), {"verdict": m.group(1), "until": time.time() + CLEARANCE_SECONDS})
            except Exception:
                pass
            return m.group(1)
    return None


def cleared() -> bool:
    return time.time() < _read(clearance_path()).get("until", 0)


def router_versions() -> dict:
    return dict(_router)


def _pypi_latest() -> str | None:
    try:
        with urllib.request.urlopen(PYPI_URL, timeout=3) as r:
            return json.loads(r.read().decode())["info"]["version"]
    except Exception:
        return None


def latest(refresh: bool = False) -> str | None:
    if _router["latest"]:
        return _router["latest"]
    cache = home() / "latest.json"
    c = _read(cache)
    if not refresh and c.get("version") and time.time() - c.get("at", 0) < _DAY:
        return c["version"]
    v = _pypi_latest()
    if v:
        _write(cache, {"version": v, "at": time.time()})
        return v
    return c.get("version")


def method() -> str:
    prefix = Path(sys.prefix).as_posix()
    if "/uv/archive-v" in prefix or "/uv/environments-v" in prefix:
        return "uvx"
    if "/uv/tools/" in prefix:
        return "uv-tool"
    if "/pipx/venvs/" in prefix:
        return "pipx"
    try:
        from importlib.metadata import distribution
        direct = distribution(PACKAGE).read_text("direct_url.json")
        if direct and json.loads(direct).get("dir_info", {}).get("editable"):
            return "source"
    except Exception:
        pass
    return "pip"


def upgrade_command(how: str | None = None) -> list[str] | None:
    how = how or method()
    if how == "uvx":
        uvx = shutil.which("uvx") or "uvx"
        return [uvx, "--refresh", f"{PACKAGE}@latest", "--version"]
    if how == "uv-tool":
        return [shutil.which("uv") or "uv", "tool", "upgrade", PACKAGE]
    if how == "pipx":
        return [shutil.which("pipx") or "pipx", "upgrade", PACKAGE]
    if how == "pip":
        return [sys.executable, "-m", "pip", "install", "-U", PACKAGE]
    return None


def hook_command(name: str, *args: str) -> list[str]:
    return [sys.executable, "-m", "hyperroute_mcp", "hook", name, *args]


def status() -> dict:
    cur, lv, floor = version(), latest(), _router["min"]
    out = {"running": cur, "latest": lv, "install": method()}
    if floor:
        out["minimum"] = floor
    out["upgrade_available"] = older(cur, lv)
    out["unsupported"] = older(cur, floor)
    cmd = upgrade_command()
    if cmd and (out["upgrade_available"] or out["unsupported"]):
        out["upgrade_command"] = " ".join(cmd)
    return out
