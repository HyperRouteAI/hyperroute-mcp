from __future__ import annotations

import os

DEFAULT_BASE_URL = "https://hyperroute.io"
DEFAULT_TIMEOUT = 30.0
DEFAULT_EXECUTE_TIMEOUT = 1200.0


def base_url() -> str:
    return os.environ.get("HYPERROUTE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def preset_api_key() -> str | None:
    return os.environ.get("HYPERROUTE_API_KEY") or None


def timeout() -> float:
    try:
        return float(os.environ.get("HYPERROUTE_TIMEOUT", DEFAULT_TIMEOUT))
    except ValueError:
        return DEFAULT_TIMEOUT


def execute_timeout() -> float:
    try:
        return float(os.environ.get("HYPERROUTE_EXECUTE_TIMEOUT", DEFAULT_EXECUTE_TIMEOUT))
    except ValueError:
        return DEFAULT_EXECUTE_TIMEOUT


def _csv(name: str) -> list[str]:
    return [p.strip() for p in (os.environ.get(name) or "").split(",") if p.strip()]


def coordinator() -> str | None:
    v = (os.environ.get("HYPERROUTE_COORDINATOR") or "").strip().lower()
    return v or None


def native_tools() -> list[str]:
    return _csv("HYPERROUTE_NATIVE_TOOLS")


def held_plans() -> list[str]:
    return _csv("HYPERROUTE_HELD")
