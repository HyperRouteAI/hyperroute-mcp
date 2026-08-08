"""Test env: never touch the developer's real token cache or a real router.

The env is set at import time — before any test module imports `hyperroute_mcp.server`, which
restores a session from disk at import.
"""

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="hyperroute-mcp-test-")
os.environ["HYPERROUTE_TOKEN_FILE"] = os.path.join(_TMP, "token.json")
os.environ["HYPERROUTE_BASE_URL"] = "http://router.test"
for _leak in ("HYPERROUTE_API_KEY", "HYPERROUTE_COORDINATOR",
              "HYPERROUTE_NATIVE_TOOLS", "HYPERROUTE_HELD"):
    os.environ.pop(_leak, None)

import pytest  # noqa: E402

from hyperroute_mcp import native  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_native_cache():
    """Coordinator ids are cached per process; a stale cache would leak across tests."""
    native.reset_cache()
    yield
    native.reset_cache()


class FakeClient:
    """Stands in for HyperRouteClient: records calls, replays canned answers."""

    CATALOG = {"tools": [
        {"id": "claude_code_opus_deep", "kind": "coordinator_agent"},
        {"id": "claude_code_sonnet_quick", "kind": "coordinator_agent"},
        {"id": "codex", "kind": "coordinator_agent"},
        {"id": "brave_search", "kind": "external_tool"},
    ]}

    def __init__(self, **answers):
        self.calls: list[tuple[str, dict]] = []
        self.answers = answers

    def _record(self, name, payload):
        self.calls.append((name, payload))

    def last(self, name: str) -> dict:
        return next(p for n, p in reversed(self.calls) if n == name)

    async def catalog(self):
        self._record("catalog", {})
        return self.answers.get("catalog", self.CATALOG)

    async def recommend_text(self, payload):
        self._record("recommend_text", payload)
        return self.answers.get("recommend_text", "session: s-1\nact: execute('brave_search', <query>)")

    async def describe(self, payload):
        self._record("describe", payload)
        return self.answers.get("describe", {"tool_id": payload["tool_id"]})

    async def execute(self, tool_id, query):
        self._record("execute", {"tool_id": tool_id, "query": query})
        return self.answers.get("execute", {"result": "ok"})
