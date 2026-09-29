from __future__ import annotations

from . import config

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

_catalog_cache: dict[str, list[str]] | None = None


def normalize_client(name: str | None) -> str:
    if not name:
        return ""
    out = []
    for ch in name.strip().lower():
        out.append(ch if ch.isalnum() else "_")
    return "".join(out).strip("_")


def product_for_client(name: str | None) -> str | None:
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
    global _catalog_cache
    if _catalog_cache is None:
        cat = await client.catalog()
        tools = cat.get("tools") if isinstance(cat, dict) else None
        if not tools:
            return []
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
    global _catalog_cache
    _catalog_cache = None


async def declared_context(client, client_name: str | None) -> dict:
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
    caller = dict(supplied or {})
    if not declared and not caller:
        return None

    merged = {**caller}
    native = _union(declared.get("native_tools"), caller.get("native_tools"))
    if native:
        merged["native_tools"] = native

    caller_ent = dict(caller.get("entitlements") or {})
    held = _union((declared.get("entitlements") or {}).get("held"), caller_ent.get("held"))
    if held or caller_ent:
        merged["entitlements"] = {**caller_ent, **({"held": held} if held else {})}
    return merged or None


def _union(*lists) -> list[str]:
    return list(dict.fromkeys([x for lst in lists for x in (lst or [])]))
