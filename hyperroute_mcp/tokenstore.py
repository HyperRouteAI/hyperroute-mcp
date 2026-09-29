from __future__ import annotations

import json
import os
from pathlib import Path


def _path() -> Path:
    p = os.environ.get("HYPERROUTE_TOKEN_FILE")
    return Path(p).expanduser() if p else Path.home() / ".hyperroute" / "token.json"


def _read_all() -> dict:
    try:
        return json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}


def _write_all(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def load(base_url: str) -> dict | None:
    entry = _read_all().get(base_url)
    return entry if entry and entry.get("api_key") else None


def save(base_url: str, api_key: str, user_id: str | None, email: str | None) -> None:
    data = _read_all()
    data[base_url] = {"api_key": api_key, "user_id": user_id, "email": email}
    _write_all(data)


def clear(base_url: str) -> None:
    data = _read_all()
    if base_url in data:
        del data[base_url]
        _write_all(data)
