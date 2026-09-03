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

    async def console(self, view, user_id):
        self._record("console", {"view": view, "user_id": user_id})
        return self.answers.get("console", {"view": view, "tools": []})

    # -- private tools ------------------------------------------------------
    SUGGESTIONS = {"suggestions": [
        {"id": "web_search_realtime", "label": "Realtime web search",
         "similarity": 0.71, "in_taxonomy": True},
        {"id": "academic_paper_search", "label": "Academic paper search",
         "similarity": 0.52, "in_taxonomy": True},
        {"id": "hotel_search_booking", "label": "Hotel booking",
         "similarity": 0.18, "in_taxonomy": False},
    ]}

    async def list_private_tools(self, project_id=None):
        self._record("list_private_tools", {"project_id": project_id})
        return self.answers.get("list_private_tools", {"tools": []})

    async def suggest_private_regions(self, name, description=None):
        self._record("suggest_private_regions", {"name": name, "description": description})
        return self.answers.get("suggest_private_regions", self.SUGGESTIONS)

    async def declare_private_tool(self, payload):
        self._record("declare_private_tool", payload)
        return self.answers.get("declare_private_tool", {
            "declared": {"tool_id": "__own__:my_search", **payload},
            "regions": [{"id": a, "label": a} for a in payload.get("anchors", [])]})

    async def update_private_tool(self, tool_id, payload):
        self._record("update_private_tool", {"tool_id": tool_id, **payload})
        return self.answers.get("update_private_tool",
                                {"updated": {"tool_id": tool_id, **payload}})

    async def set_private_stance(self, tool_id, stance, project_id=None):
        self._record("set_private_stance", {"tool_id": tool_id, "stance": stance})
        return self.answers.get("set_private_stance", {"updated": {"tool_id": tool_id}})

    async def delete_private_tool(self, tool_id, project_id=None):
        self._record("delete_private_tool", {"tool_id": tool_id})
        return self.answers.get("delete_private_tool", {"deleted": True})
