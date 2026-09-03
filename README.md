# hyperroute-mcp

The official [Model Context Protocol](https://modelcontextprotocol.io) server for
**[HyperRoute](https://hyperroute.io)**.

HyperRoute is a router for AI agents. Give it a task and it picks the best external tool for
*that* task — measured, not advertised — then runs the tool for you with your own key held
server-side, and learns from how it went. This MCP server is how a coordinator agent (Claude
Code, Codex, Goose, Cursor, LangGraph, …) drives it:

> recommend → onboard a key → execute the tool server-side → report the outcome

It talks to the router only over its public HTTP API and holds no product logic of its own.

## Why route at all

An agent with 100 tools bolted on has a context problem and a quality problem. HyperRoute
replaces both with one verb: your agent learns `recommend`, and HyperRoute decides which of
hundreds of tools actually answers this task, whether you can already do it better yourself,
and what it will cost.

- **Measured, not advertised.** Every capability score is backed by real graded probes you can
  inspect (`describe(tool_id, ["evidence"])`).
- **Your keys never leave the server.** You connect a key once; HyperRoute runs the tool with it
  and returns only the result. The key is never sent to your agent, never logged.
- **It tells you when NOT to route.** If nothing beats what your coordinator already does, the
  verdict is `use_native` — do it yourself. That only works if the server knows which coordinator
  it runs inside; see [Declaring your coordinator](#declaring-your-coordinator).

## Install

```bash
pip install hyperroute-mcp
```

Or with [`pipx`](https://pipx.pypa.io), so the command is always on your PATH regardless of which
virtualenv is active — which is what MCP clients need, since they launch the server themselves:

```bash
pipx install hyperroute-mcp
```

From source, for development:

```bash
git clone https://github.com/HyperRouteAI/hyperroute-mcp
cd hyperroute-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

Requires Python ≥ 3.10.

## Add it to your coordinator

**Claude Code**

```bash
claude mcp add hyperroute -- hyperroute-mcp
```

If you installed into a virtualenv rather than with pipx, `hyperroute-mcp` is only on your PATH while
that venv is active — and MCP clients launch the server themselves, outside your shell. Give them the
absolute path in that case: `/path/to/.venv/bin/hyperroute-mcp`.

**OpenCode** — copy [`opencode.json`](opencode.json) into your project. OpenCode is bring-your-own-model,
so the server can't infer what you're running from the client name alone: set `HYPERROUTE_COORDINATOR`
(or `HYPERROUTE_NATIVE_TOOLS`) to match the model you actually point it at, or HyperRoute will have no
baseline for you. [`AGENTS.md`](AGENTS.md) carries the operating loop and the methodology — drop it in
so the agent can both act correctly and explain how the routing works.

**Any MCP client** (`mcp.json` / `claude_desktop_config.json` / equivalent):

```json
{
  "mcpServers": {
    "hyperroute": {
      "command": "hyperroute-mcp",
      "env": { "HYPERROUTE_BASE_URL": "https://hyperroute.io" }
    }
  }
}
```

Then just ask: *"Use HyperRoute to find the best tool for searching recent papers, connect my
key, and run it."* The agent calls `recommend` → `connect_info` → `onboard` → `execute` on its
own.

## Authenticate once

`recommend` and browsing are public — no account. Connecting keys and running tools need one.

Preferred: mint a personal access token at [hyperroute.io](https://hyperroute.io) and hand it to
the `use_token` tool (or set `HYPERROUTE_API_KEY`). Your password never enters the conversation.

The token is then **cached on disk** (`~/.hyperroute/token.json`, mode `0600`, keyed by router
URL), so every new MCP session restores your login silently. You are asked to authenticate again
only if the router invalidates the token. A full inline `register` → `verify` email-code flow is
also available for headless use.

## Declaring your coordinator

HyperRoute compares external tools against *what you can already do*. That baseline is the set of
coordinators that are free to you — and it is **empty by default**, because the router never
assumes you have one. An MCP server that does not declare itself gets an external tool
recommended for every task, including tasks the coordinator does better itself.

This server declares it for you. It reads the MCP client identity your coordinator sends on
connect and maps it to the coordinator HyperRoute models (`claude-code` → `claude_code`, …).
Check what it resolved with the `session_info` tool: if `native_tools` is empty, set it yourself.

```jsonc
"env": {
  "HYPERROUTE_COORDINATOR": "claude_code",       // or codex / cursor / goose / …; "none" disables
  "HYPERROUTE_HELD": "anthropic_max_5x"          // subscriptions you already pay for → priced at $0
}
```

`HYPERROUTE_NATIVE_TOOLS` takes exact tool ids if you want to pin one model variant instead of the
whole product family.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `HYPERROUTE_BASE_URL` | `https://hyperroute.io` | Which router to talk to. Override to point at a different instance. |
| `HYPERROUTE_API_KEY` | — | `hyr_…` token to start already logged in. Externally managed: used, never cached. |
| `HYPERROUTE_TIMEOUT` | `30` | Per-request timeout, seconds. |
| `HYPERROUTE_TOKEN_FILE` | `~/.hyperroute/token.json` | Where the cached login lives. |
| `HYPERROUTE_COORDINATOR` | auto-detect | Which coordinator this runs inside; `none` disables the declaration. |
| `HYPERROUTE_NATIVE_TOOLS` | — | Exact coordinator tool ids, overriding detection. |
| `HYPERROUTE_HELD` | — | Comma-separated plan groups you hold, e.g. `anthropic_max_5x`. |

## Tools

| Tool | What it does |
|---|---|
| `session_info` | Base URL, login state, and the coordinator this server declares. Call first. |
| `health` | Router readiness + the loaded model bundle. |
| `recommend` | **The main verb.** Task → ranked tools as a compact table + how to act. Public. |
| `describe` | Pull ONE tool's depth on demand: `about` · `price` · `facets` · `evidence`. |
| `facets_catalog` | Every facet a tool can be judged on, with defaults. Fetch once. |
| `get_preferences` / `set_preferences` | Your standing constraints, applied to every future route. |
| `connect_info` | A tool's onboarding process: signup URL, steps, whether you're connected. |
| `onboard` | Save + test one tool API key under your account. Stored encrypted, reused forever. |
| `list_credentials` | Your connected tools (keys masked). |
| `execute` | Run the chosen tool server-side with your held key; returns only the result. |
| `fetch_result` | Page through a result too large to inline. |
| `report_outcome` | Per-call feedback — the signal that sharpens future routing. |
| `report_narrative` | Open-ended feedback about a whole run. |
| `console` | Human-readable management view: history, tools, keys, stats. |
| `my_tools` / `declare_my_tool` / `update_my_tool` / `remove_my_tool` | **Your own tools.** Tell HyperRoute about a tool you already have and what it's for; it then routes to it by name for that kind of work. |
| `suggest_my_tool_regions` | Preview which capabilities a description maps onto, before declaring. |
| `my_tool_report` | Your own track record on your declared tools, per capability. |
| `use_token` / `register` / `verify` / `login` / `login_link` / `verify_login` / `forgot_password` / `whoami` | Account lifecycle. |

### The wire is deliberately lean

`recommend` answers with a compact table, not a catalog dump:

```
session: s-6d6c5a95f9f84d9a
verdict: interpose
refine:  freshness, cited_references, source_quality

  tool              name                        price  use        why
→ opencitations     OpenCitations Index         free   ready      highest-ranked: capability 0.81 …
  semantic_scholar  Semantic Scholar Graph API  free   needs_key  lower capability (0.75 vs 0.81).

confidence: med (on the pick)
act: execute('opencitations', <query>)
```

Everything else — descriptions, per-plan pricing, per-facet breakdowns, the probe evidence behind
a score — is pulled for the one tool that matters via `describe`. That keeps a route roughly an
order of magnitude cheaper in tokens than shipping the full object on every call.

The `use` column is the whole auth story: `ready` (run it) · `needs_key` (connect first) ·
`native` (do it yourself) · `soon` (not runnable server-side yet).

### Your own tools

Tell your agent *"I have my own web search, always use it for research"* and it calls
`declare_my_tool` with your words. HyperRoute maps them onto named capability regions and, from
then on, routes to **your** tool for that kind of work — in every session, with a `use_own`
verdict — instead of ranking a catalog tool. Your agent runs it with the access it already has;
HyperRoute never executes it and holds no key for it.

Two things it deliberately does *not* do:

- **It is scoped.** Outside the region you declared, your tool is not in the ranking at all. A
  declaration is never a blanket override.
- **It is unscored.** HyperRoute has never tested your tool, so it carries no capability number
  and never pretends to. It wins because you said so.

`my_tool_report` later shows your **own** outcome record per capability, beside whether HyperRoute
holds tested alternatives there. If you want a better-scoring catalog tool to be able to displace
yours in some region, switch that tool to `stance="benchmarked"`.

### Two-pass refinement

Pass 1 always returns a usable ranking. The `refine:` line names the unset preferences that would
reorder *these* candidates; fill the relevant ones and call `recommend` again with `facets` for a
personalized result. Durable constraints (GDPR, a budget cap) belong in `set_preferences`
instead — stored once, applied to every future route.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

The suite is fully offline — the router is faked, so no network and no real account are touched.
Set `HYPERROUTE_BASE_URL` to try it against a different router instance.

## License

MIT — see [LICENSE](LICENSE).
