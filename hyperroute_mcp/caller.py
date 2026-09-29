from __future__ import annotations

import asyncio
import glob
import json
import os
import platform
import re
import sqlite3
from importlib import resources
from pathlib import Path
from typing import Any

HEADER = "x-hyperroute-caller"
UNKNOWN = "unknown"
_TOKEN = re.compile(r"^[A-Za-z0-9._:/@+\-]{1,64}$")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TEMPLATE = re.compile(r"\{([^{}]+)\}")
_KINDS = {"env", "env_set", "const", "json", "toml", "jsonl", "sqlite"}
_TAIL_BYTES = 512 * 1024
_BUDGET_S = 0.25
_SILENT_LIMIT = 3


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("hyperroute-mcp")
    except Exception:
        return "0"


def _roots() -> list[Path]:
    home = Path.home()
    dirs = [
        os.environ.get("CLAUDE_CONFIG_DIR") or home / ".claude",
        os.environ.get("CODEX_HOME") or home / ".codex",
        Path(os.environ.get("XDG_DATA_HOME") or home / ".local/share") / "opencode",
        Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "opencode",
    ]
    out = []
    for d in dirs:
        try:
            out.append(Path(d).expanduser().resolve())
        except Exception:
            pass
    return out


def _allowed(p: Path) -> bool:
    try:
        rp = p.resolve()
    except Exception:
        return False
    return any(rp == r or r in rp.parents for r in _roots())


def token(v: Any) -> str | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        v = str(v)
    return v if isinstance(v, str) and _TOKEN.match(v) else None


def _dig(obj: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj


def _fill(s: str, vars: dict) -> str | None:
    missing = False

    def sub(m: re.Match) -> str:
        nonlocal missing
        key = m.group(1)
        if key.startswith("env."):
            name, _, default = key[4:].partition("|")
            return os.environ.get(name) or default
        v = vars.get(key)
        if v is None:
            missing = True
            return ""
        return str(v)

    out = _TEMPLATE.sub(sub, s)
    return None if missing else os.path.expanduser(out)


def _natural(p: str) -> list:
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", p)]


def _pick(pattern: str, newest: bool) -> Path | None:
    hits = [h for h in glob.glob(pattern) if os.path.isfile(h)]
    if not hits:
        return None
    if newest:
        hits.sort(key=lambda h: os.path.getmtime(h))
    else:
        hits.sort(key=_natural)
    p = Path(hits[-1])
    return p if _allowed(p) else None


def _read_env(probe: dict, vars: dict) -> dict:
    return {out: os.environ.get(var) for var, out in probe.get("out", {}).items()}


def _read_env_set(probe: dict, vars: dict) -> dict:
    return dict(probe.get("set") or {}) if os.environ.get(probe.get("var", "")) else {}


def _read_const(probe: dict, vars: dict) -> dict:
    return dict(probe.get("set") or {})


def _read_json(probe: dict, vars: dict) -> dict:
    path = _fill(probe.get("path", ""), vars)
    p = _pick(path, newest=True) if path else None
    if p is None:
        return {}
    data = json.loads(p.read_text())
    return {out: _dig(data, src) for src, out in probe.get("out", {}).items()}


def _read_toml(probe: dict, vars: dict) -> dict:
    try:
        import tomllib
    except ImportError:
        return {}
    path = _fill(probe.get("path", ""), vars)
    p = _pick(path, newest=True) if path else None
    if p is None:
        return {}
    data = tomllib.loads(p.read_text())
    return {out: _dig(data, src) for src, out in probe.get("out", {}).items()}


def _read_jsonl(probe: dict, vars: dict) -> dict:
    path = _fill(probe.get("path", ""), vars)
    p = _pick(path, newest=True) if path else None
    if p is None:
        return {}
    with p.open("rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - _TAIL_BYTES))
        lines = f.read().decode("utf-8", "replace").splitlines()
    want = dict(probe.get("out", {}))
    got: dict = {}
    for line in reversed(lines[-int(probe.get("tail", 400)):]):
        if not want:
            break
        try:
            row = json.loads(line)
        except ValueError:
            continue
        for src in list(want):
            v = _dig(row, src)
            if v is not None:
                got[want.pop(src)] = v
    return got


def _read_sqlite(probe: dict, vars: dict) -> dict:
    path = _fill(probe.get("path", ""), vars)
    p = _pick(path, newest=False) if path else None
    table = probe.get("table", "")
    order = probe.get("order", "")
    where = probe.get("where") or {}
    jcol = probe.get("json")
    out = probe.get("out", {})
    if p is None or not _IDENT.match(table) or (order and not _IDENT.match(order)):
        return {}
    params = {}
    for col, tmpl in where.items():
        v = _fill(str(tmpl), vars)
        if v is None or not _IDENT.match(col):
            return {}
        params[col] = v
    con = sqlite3.connect(f"{p.as_uri()}?mode=ro", uri=True, timeout=0.1)
    try:
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if not cols or any(c not in cols for c in params) or (order and order not in cols):
            return {}
        if jcol:
            if jcol not in cols:
                return {}
            select = [jcol]
        else:
            select = [c for c in out if c in cols]
            if not select:
                return {}
        sql = f"SELECT {', '.join(select)} FROM {table}"
        if params:
            sql += " WHERE " + " AND ".join(f"{c} = ?" for c in params)
        if order:
            sql += f" ORDER BY {order} DESC"
        sql += f" LIMIT {int(probe.get('scan', 1)) if jcol else 1}"
        rows = con.execute(sql, list(params.values())).fetchall()
    finally:
        con.close()
    if not jcol:
        return {out[c]: v for c, v in zip(select, rows[0], strict=False)} if rows else {}
    cond = probe.get("json_where") or {}
    for (raw,) in rows:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if all(_dig(data, k) == v for k, v in cond.items()):
            return {dst: _dig(data, src) for src, dst in out.items()}
    return {}


_READERS = {
    "env": _read_env, "env_set": _read_env_set, "const": _read_const, "json": _read_json,
    "toml": _read_toml, "jsonl": _read_jsonl, "sqlite": _read_sqlite,
}


def valid(manifest: Any) -> bool:
    if not isinstance(manifest, dict) or not isinstance(manifest.get("agents"), dict):
        return False
    for spec in manifest["agents"].values():
        if not isinstance(spec, dict) or not isinstance(spec.get("probes"), list):
            return False
        for pr in spec["probes"]:
            if not isinstance(pr, dict) or pr.get("kind") not in _KINDS or not token(pr.get("id")):
                return False
    return True


def builtin() -> dict:
    return json.loads(resources.files("hyperroute_mcp").joinpath("collectors.json").read_text())


def _norm(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def detect(manifest: dict, client_name: str | None) -> str | None:
    norm = _norm(client_name)
    ai = (os.environ.get("AI_AGENT") or "").lower()
    for agent, spec in manifest.get("agents", {}).items():
        m = spec.get("match") or {}
        if norm and any(norm == c or norm.startswith(c + "_") for c in m.get("client", [])):
            return agent
    for agent, spec in manifest.get("agents", {}).items():
        m = spec.get("match") or {}
        if any(os.environ.get(e) for e in m.get("env", [])) or (ai and ai in m.get("ai_agent", [])):
            return agent
    return None


def collect(manifest: dict, client: dict) -> dict:
    profile: dict = {
        "mcp_client": token(client.get("name")),
        "mcp_client_version": token(client.get("version")),
        "mcp_protocol": token(client.get("protocol")),
        "agent_version": token(client.get("version")),
        "collector": token(_version()),
        "os": token(platform.system().lower()),
        "arch": token(platform.machine().lower()),
        "origin": "self",
    }
    agent = detect(manifest, client.get("name"))
    profile["agent"] = agent or token(_norm(client.get("name"))) or token(os.environ.get("AI_AGENT"))
    ok, missed = [], []
    if agent:
        try:
            cwd = os.getcwd()
        except OSError:
            cwd = None
        vars: dict = {"_cwd": cwd} if cwd else {}
        for probe in manifest["agents"][agent].get("probes", []):
            pid = probe.get("id")
            if probe.get("if_unset") and vars.get(probe["if_unset"]):
                continue
            try:
                got = _READERS[probe["kind"]](probe, vars)
            except Exception:
                got = {}
            maps = probe.get("map") or {}
            hit = False
            for field, value in got.items():
                if value is None:
                    continue
                if field in maps:
                    value = maps[field].get(str(value))
                if field.startswith("_"):
                    if value is not None and field not in vars:
                        vars[field] = value
                        hit = True
                    continue
                t = token(value)
                if t:
                    hit = True
                    if profile.get(field) is None:
                        profile[field] = t
            (ok if hit else missed).append(pid)
    profile["probes_ok"] = sorted(ok) or None
    profile["probes_missed"] = sorted(missed) or None
    return {k: v for k, v in profile.items() if v is not None}


class Caller:
    def __init__(self) -> None:
        self.client: dict = {}
        self.manifest: dict | None = None
        self.ref: str | None = None
        self.acked: dict | None = None
        self.last: dict | None = None
        self.silent = 0
        self.pending: asyncio.Future | None = None

    @property
    def enabled(self) -> bool:
        if self.silent >= _SILENT_LIMIT:
            return False
        return (os.environ.get("HYPERROUTE_CALLER") or "").strip().lower() not in {"host", "off"}

    def seen(self, name: Any, version: Any, protocol: Any = None) -> None:
        if name:
            self.client = {"name": name, "version": version,
                           "protocol": protocol or self.client.get("protocol")}

    async def load(self, fetch) -> None:
        if self.manifest is not None:
            return
        try:
            got = await fetch()
        except Exception:
            got = None
        if valid(got):
            self.manifest = got
            return
        try:
            self.manifest = builtin()
        except Exception:
            self.manifest = {"agents": {}}

    async def block(self) -> tuple[dict | None, dict | None]:
        try:
            if not self.enabled or self.manifest is None:
                return None, None
            if self.pending is None or self.pending.done():
                self.pending = asyncio.ensure_future(
                    asyncio.to_thread(collect, self.manifest, dict(self.client)))
            try:
                profile = await asyncio.wait_for(asyncio.shield(self.pending), _BUDGET_S)
                self.last = profile
            except Exception:
                profile = self.last
            if not profile:
                return None, None
            return self._shape(profile)
        except Exception:
            return None, None

    def _shape(self, profile: dict) -> tuple[dict | None, dict | None]:
        if self.ref and self.acked is not None:
            if profile == self.acked:
                return {"ref": self.ref}, profile
            changed = {k: v for k, v in profile.items() if self.acked.get(k) != v}
            gone = [k for k in self.acked if k not in profile]
            out: dict = {"ref": self.ref}
            if changed:
                out["set"] = changed
            if gone:
                out["unset"] = gone
            return out, profile
        return {"set": profile}, profile

    def ack(self, header: str | None, profile: dict | None, ok: bool = True) -> None:
        try:
            if profile is None:
                return
            if not header:
                if ok:
                    self.silent += 1
                return
            self.silent = 0
            if header == UNKNOWN:
                self.ref, self.acked = None, None
            else:
                self.ref, self.acked = header, profile
        except Exception:
            pass
