"""The native-baseline declaration: without it the router has nothing to compare against and an
external tool wins every task, so these assertions guard a routing-correctness property, not a
formatting one."""

import pytest

from hyperroute_mcp import native

from .conftest import FakeClient


@pytest.mark.parametrize("name,expected", [
    ("claude-code", "claude_code"),
    ("Claude Code", "claude_code"),
    ("claude-code/2.1.0", "claude_code"),
    ("codex", "codex"),
    ("cursor", "cursor"),
    ("mcp-inspector", None),
    ("claude", None),          # ambiguous: Desktop is not Claude Code — declare nothing
    ("", None),
    (None, None),
])
def test_product_for_client(name, expected):
    assert native.product_for_client(name) == expected


async def test_coordinator_ids_resolve_from_catalog_and_cache():
    c = FakeClient()
    assert await native.coordinator_ids(c, "claude_code") == [
        "claude_code_opus_deep", "claude_code_sonnet_quick"]
    assert await native.coordinator_ids(c, "codex") == ["codex"]
    # one catalog fetch for the whole process, not one per lookup
    assert sum(1 for n, _ in c.calls if n == "catalog") == 1


async def test_coordinator_ids_unreachable_catalog_declares_nothing():
    assert await native.coordinator_ids(FakeClient(catalog={"_error": True}), "claude_code") == []


async def test_declared_context_from_client_identity():
    ctx = await native.declared_context(FakeClient(), "claude-code")
    assert ctx["native_tools"] == ["claude_code_opus_deep", "claude_code_sonnet_quick"]
    assert "entitlements" not in ctx


async def test_declared_context_unknown_client_is_empty():
    assert await native.declared_context(FakeClient(), "some-random-client") == {}


async def test_env_coordinator_overrides_detection(monkeypatch):
    monkeypatch.setenv("HYPERROUTE_COORDINATOR", "codex")
    ctx = await native.declared_context(FakeClient(), "claude-code")
    assert ctx["native_tools"] == ["codex"]


async def test_env_native_tools_pins_exact_ids_without_a_catalog_fetch(monkeypatch):
    monkeypatch.setenv("HYPERROUTE_NATIVE_TOOLS", "claude_code_sonnet_quick")
    c = FakeClient()
    ctx = await native.declared_context(c, "claude-code")
    assert ctx["native_tools"] == ["claude_code_sonnet_quick"]
    assert not any(n == "catalog" for n, _ in c.calls)


async def test_coordinator_none_disables_the_declaration(monkeypatch):
    monkeypatch.setenv("HYPERROUTE_COORDINATOR", "none")
    monkeypatch.setenv("HYPERROUTE_HELD", "anthropic_max_5x")
    assert await native.declared_context(FakeClient(), "claude-code") == {}


async def test_held_plans_ride_along(monkeypatch):
    monkeypatch.setenv("HYPERROUTE_HELD", "anthropic_max_5x, openai_pro")
    ctx = await native.declared_context(FakeClient(), "claude-code")
    assert ctx["entitlements"] == {"held": ["anthropic_max_5x", "openai_pro"]}


def test_merge_context_lets_the_caller_win():
    declared = {"native_tools": ["claude_code_opus_deep"], "entitlements": {"held": ["x"]}}
    merged = native.merge_context(declared, {"native_tools": [], "usage": {"monthly_queries": 10}})
    assert merged["native_tools"] == []                      # an explicit override, even empty
    assert merged["entitlements"] == {"held": ["x"]}          # untouched keys survive
    assert merged["usage"] == {"monthly_queries": 10}


def test_merge_context_passthrough_when_nothing_declared():
    assert native.merge_context({}, None) is None
    assert native.merge_context({}, {"a": 1}) == {"a": 1}
