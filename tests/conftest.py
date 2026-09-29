import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="hyperroute-mcp-test-")
os.environ["HYPERROUTE_TOKEN_FILE"] = os.path.join(_TMP, "token.json")
os.environ["HYPERROUTE_BASE_URL"] = "http://router.test"
os.environ["HYPERROUTE_HOME"] = os.path.join(_TMP, "home")
os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(_TMP, "claude")
for _leak in ("HYPERROUTE_API_KEY", "HYPERROUTE_COORDINATOR",
              "HYPERROUTE_NATIVE_TOOLS", "HYPERROUTE_HELD"):
    os.environ.pop(_leak, None)

import pytest

from hyperroute_mcp import install, native


@pytest.fixture(autouse=True)
def _offline_versions(monkeypatch):
    monkeypatch.setattr(install, "_pypi_latest", lambda: None)
    monkeypatch.setattr(install, "_router", {"latest": None, "min": None})


@pytest.fixture(autouse=True)
def _clean_native_cache():
    native.reset_cache()
    yield
    native.reset_cache()


class FakeClient:

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

    async def health(self):
        self._record("health", {})
        return self.answers.get("health", {"ready": True, "routable": True})

    async def whoami(self):
        self._record("whoami", {})
        return self.answers.get("whoami", {"email": "a@x.io", "tier": "free", "status": "active"})

    async def facets_catalog(self):
        self._record("facets_catalog", {})
        return self.answers.get("facets_catalog", {"facets": []})

    async def catalog(self):
        self._record("catalog", {})
        return self.answers.get("catalog", self.CATALOG)

    async def recommend_text(self, payload):
        self._record("recommend_text", payload)
        return self.answers.get("recommend_text", "session: s-1\nact: execute('brave_search', <query>)")

    async def describe(self, payload):
        self._record("describe", payload)
        return self.answers.get("describe", {"tool_id": payload["tool_id"]})

    async def execute(self, tool_id, query, session_id=None):
        self._record("execute", {"tool_id": tool_id, "query": query, "session_id": session_id})
        return self.answers.get("execute", {"result": "ok"})

    async def console(self, view, user_id):
        self._record("console", {"view": view, "user_id": user_id})
        return self.answers.get("console", {"view": view, "tools": []})

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

    async def declare_private_tool(self, payload):
        self._record("declare_private_tool", payload)
        return self.answers.get("declare_private_tool", {
            "declared": {"tool_id": "__own__:my_search", **payload},
            "regions": [{"id": a, "label": a} for a in payload.get("anchors", [])]})

    async def update_private_tool(self, tool_id, payload):
        self._record("update_private_tool", {"tool_id": tool_id, **payload})
        return self.answers.get("update_private_tool",
                                {"updated": {"tool_id": tool_id, **payload}})

    async def delete_private_tool(self, tool_id, project_id=None):
        self._record("delete_private_tool", {"tool_id": tool_id})
        return self.answers.get("delete_private_tool", {"deleted": True})

    async def list_preferred_tools(self, project_id=None):
        self._record("list_preferred_tools", {"project_id": project_id})
        return self.answers.get("list_preferred_tools", {"tools": []})

    async def prefer_tool(self, payload):
        self._record("prefer_tool", payload)
        return self.answers.get("prefer_tool", {
            "preferred": {"tool_id": "kagi_search", "margin": payload.get("margin") or 0.1,
                          "note": payload.get("note") or "", "name": "Kagi Search"},
            "tool": {"id": "kagi_search", "name": "Kagi Search"}})

    async def delete_preferred_tool(self, tool_id, project_id=None):
        self._record("delete_preferred_tool", {"tool_id": tool_id})
        return self.answers.get("delete_preferred_tool", {"deleted": True})
