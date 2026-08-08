"""Runtime configuration — everything is environment-driven, so the same package runs against
the hosted router, a self-hosted one, or a local dev instance with no code change.

  HYPERROUTE_BASE_URL      base URL of the router (default https://hyperroute.io)
  HYPERROUTE_API_KEY       optional hyr_… personal access token to start already logged in
  HYPERROUTE_TIMEOUT       per-request timeout in seconds (default 30)
  HYPERROUTE_TOKEN_FILE    where the login token is cached (default ~/.hyperroute/token.json)

  HYPERROUTE_COORDINATOR   which coordinator this server runs inside — "claude_code", "codex",
                           or "none" to disable the declaration entirely. Unset = auto-detect
                           from the MCP client's identity (see native.py).
  HYPERROUTE_NATIVE_TOOLS  explicit, comma-separated coordinator tool ids to declare instead of
                           auto-detecting (e.g. "claude_code_sonnet_quick" to pin one model).
  HYPERROUTE_HELD          comma-separated plan groups the user already pays for
                           (e.g. "anthropic_max_5x"), so those tools price at $0.
"""

from __future__ import annotations

import os

DEFAULT_BASE_URL = "https://hyperroute.io"
DEFAULT_TIMEOUT = 30.0


def base_url() -> str:
    return os.environ.get("HYPERROUTE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def preset_api_key() -> str | None:
    return os.environ.get("HYPERROUTE_API_KEY") or None


def timeout() -> float:
    try:
        return float(os.environ.get("HYPERROUTE_TIMEOUT", DEFAULT_TIMEOUT))
    except ValueError:
        return DEFAULT_TIMEOUT


def _csv(name: str) -> list[str]:
    return [p.strip() for p in (os.environ.get(name) or "").split(",") if p.strip()]


def coordinator() -> str | None:
    """The coordinator product to declare, or None to auto-detect. "none"/"off" disables it."""
    v = (os.environ.get("HYPERROUTE_COORDINATOR") or "").strip().lower()
    return v or None


def native_tools() -> list[str]:
    """Explicit coordinator tool ids to declare, bypassing detection."""
    return _csv("HYPERROUTE_NATIVE_TOOLS")


def held_plans() -> list[str]:
    """Plan groups the user holds — a held plan makes that tool free to them."""
    return _csv("HYPERROUTE_HELD")
