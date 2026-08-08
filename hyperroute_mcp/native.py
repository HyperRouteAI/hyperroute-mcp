"""Declaring the native baseline — the coordinator this server runs inside.

HyperRoute never assumes you have a coordinator. Its verdict for a task is one of:

  * **interpose** — an external tool beats what you can already do, so route to it;
  * **use_native** — nothing beats your own tools, so do it yourself.

That comparison needs a baseline, and the baseline is the set of coordinators that are
effectively free to the caller. An MCP server that does not say which coordinator it runs
inside gives the router no baseline at all, so an external tool wins *every* time — including
for tasks the coordinator does better itself. Declaring it is therefore not optional.

Two independent ways to be effectively free, both sent on the `recommend` call's `context`:

  * `native_tools` — **self-loopback**: the caller IS this coordinator. Zero marginal cost.
  * `entitlements.held` — a **subscription** the user already pays for (e.g. `anthropic_max_5x`),
    which also prices that tool at $0.

Resolution order, first hit wins:

  1. `HYPERROUTE_NATIVE_TOOLS` — explicit tool ids, e.g. to pin one model variant.
  2. `HYPERROUTE_COORDINATOR` — a product name (`claude_code`, `codex`); `none`/`off` disables.
  3. Auto-detect from the MCP client's own identity, sent on the protocol handshake.

A product name resolves to concrete tool ids against the router's live catalog (fetched once
per process, cached), so a new coordinator variant needs no release here. If detection finds
nothing the declaration is simply omitted — routing still works, it just has no baseline.
"""

from __future__ import annotations

from . import config

# MCP client identity (as sent on the protocol handshake) -> the coordinator product it is.
# Matched as a prefix on the lowercased, punctuation-normalized client name, longest first, so
# "claude-code" wins over a bare "claude". Deliberately conservative: an unrecognized client
# declares nothing rather than claiming capability it does not have.
_CLIENT_PRODUCTS = {
    "claude_code": "claude_code",
    "codex": "codex",
    "cursor": "cursor",
    "goose": "goose",
    "cline": "cline",
    "opencode": "opencode",
    "aider": "aider",
}

_DISABLED = {"none", "off", "no", "false", "0"}

# Resolved once per process: product name -> the coordinator tool ids the router models for it.
_catalog_cache: dict[str, list[str]] | None = None


def normalize_client(name: str | None) -> str:
    """`Claude Code`, `claude-code`, `claude_code/1.2` -> `claude_code`."""
    if not name:
        return ""
    out = []
    for ch in name.strip().lower():
        out.append(ch if ch.isalnum() else "_")
    return "".join(out).strip("_")


def product_for_client(name: str | None) -> str | None:
    """Which coordinator product an MCP client is, or None when we don't recognize it."""
    norm = normalize_client(name)
    if not norm:
        return None
    for key in sorted(_CLIENT_PRODUCTS, key=len, reverse=True):
        if norm == key or norm.startswith(key + "_"):
            return _CLIENT_PRODUCTS[key]
    return None


def _match(tool_id: str, product: str) -> bool:
    return tool_id == product or tool_id.startswith(product + "_")


async def coordinator_ids(client, product: str) -> list[str]:
    """The router's tool ids for a coordinator product, read from its live catalog.

    All of a product's variants are declared together: the coordinator can switch model or
    depth mid-session, so the product — not one variant — is what the caller *is*. Pin a single
    variant with `HYPERROUTE_NATIVE_TOOLS` when that matters.
    """
    global _catalog_cache
    if _catalog_cache is None:
        cat = await client.catalog()
        tools = cat.get("tools") if isinstance(cat, dict) else None
        if not tools:
            return []                                    # unreachable/empty catalog: declare nothing
        by_product: dict[str, list[str]] = {}
        for t in tools:
            if t.get("kind") != "coordinator_agent":
                continue
            tid = t.get("id") or ""
            for prod in set(_CLIENT_PRODUCTS.values()):
                if _match(tid, prod):
                    by_product.setdefault(prod, []).append(tid)
        _catalog_cache = by_product
    return sorted(_catalog_cache.get(product, []))


def reset_cache() -> None:
    """Drop the cached catalog (tests; a router that swapped bundles mid-session)."""
    global _catalog_cache
    _catalog_cache = None


async def declared_context(client, client_name: str | None) -> dict:
    """The `context` fragment to merge into every route: what this caller already has.

    Returns `{}` when nothing is known — no baseline, and the best external tool simply wins.
    """
    setting = config.coordinator()
    if setting in _DISABLED:
        return {}

    ids = config.native_tools()
    if not ids:
        product = setting or product_for_client(client_name)
        if product:
            ids = await coordinator_ids(client, product)

    out: dict = {}
    if ids:
        out["native_tools"] = ids
    held = config.held_plans()
    if held:
        out["entitlements"] = {"held": held}
    return out


def merge_context(declared: dict, supplied: dict | None) -> dict | None:
    """Merge the declaration under a caller-supplied `context` — an explicit value always wins,
    including an explicit empty one, so a coordinator can override what we detected."""
    if not declared:
        return supplied
    merged = dict(declared)
    for k, v in (supplied or {}).items():
        merged[k] = v
    return merged
