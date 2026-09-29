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
  it runs inside; see [Your coordinator](#your-coordinator).

## Install

In your coding agent, type:

```
! uvx -q hyperroute-mcp@latest install
```

It adds HyperRoute to Claude Code, with hooks so the agent and its subagents use it; `install --remove`
undoes the hooks. Needs
[uv](https://docs.astral.sh/uv/).

**By hand.** Claude Code:

```bash
claude mcp add --scope user hyperroute -- uvx hyperroute-mcp@latest
```

then ask the agent to *"finish HyperRoute setup"*. Any other MCP client:

```json
{ "mcpServers": { "hyperroute": { "command": "uvx", "args": ["hyperroute-mcp@latest"] } } }
```

## Setup, checks and upgrades

Ask your agent to set up, check, upgrade or remove HyperRoute; it calls `setup`, shows you what it
would change, and applies it when you agree. `hyperroute-mcp install --remove` also undoes it.

## Log in

Routing needs an account. On [hyperroute.io](https://hyperroute.io), open Connect and copy the login
line from the MCP tab into your agent; it calls `use_token`. Email and password (`login`) also work.
The login is saved and reused in every later session.

## Your coordinator

HyperRoute compares external tools against what your coordinator can already do. The server reads
which coordinator launched it and declares it for you; `session_info` shows what it resolved.

## Tools

| Tool | What it does |
|---|---|
| `recommend` | Task → ranked tools and how to act. |
| `execute` | Run the chosen tool with your connected key; returns the result. |
| `connect_info` / `onboard` | A tool's signup steps; save and test its API key. |
| `report_outcome` / `report_narrative` | Feedback on a call, or on a whole run. |
| `describe` | One tool's details: `about` · `price` · `facets` · `evidence`. |
| `fetch_result` | Page through a large result. |
| `get_preferences` / `set_preferences` | Preferences applied to every route, and every facet you can set. |
| `console` | History, connected keys, tools, stats. |
| `my_tools` / `declare_my_tool` / `update_my_tool` / `remove_my_tool` | Tools you already have, when to use them, and how they did. |
| `my_preferred_tools` / `prefer_tool` / `unprefer_tool` | Catalog tools you favour. |
| `session_info` / `setup` | Connection, login and versions; set up, check, upgrade or remove. |
| `use_token` / `login` | Log in. |

A ranking looks like:

```
session: s-6d6c5a95f9f84d9a
verdict: interpose

  tool              name                        price  use        why
→ opencitations     OpenCitations Index         free   ready      highest-ranked: capability 0.81 …
  semantic_scholar  Semantic Scholar Graph API  free   needs_key  lower capability (0.75 vs 0.81).

confidence: med (on the pick)
act: execute('opencitations', <query>)
```

`use`: `ready` (run it) · `needs_key` (connect a key first) · `native` (the agent does it itself) ·
`own` (your own tool) · `soon` (not runnable yet).

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

The suite is fully offline — the router is faked, so no network and no real account are touched.

### Environment variables

None are needed against the hosted router. For development and self-hosting:

| Variable | Default | Meaning |
|---|---|---|
| `HYPERROUTE_BASE_URL` | `https://hyperroute.io` | Router to talk to (local dev or self-hosted). |
| `HYPERROUTE_API_KEY` | — | `hyr_…` token to start logged in; used, never cached. |
| `HYPERROUTE_TIMEOUT` | `30` | Per-request timeout, seconds. |
| `HYPERROUTE_TOKEN_FILE` | `~/.hyperroute/token.json` | Cached login. |
| `HYPERROUTE_HOME` | `~/.hyperroute` | Setup record and version cache. |
| `HYPERROUTE_COORDINATOR` | auto-detect | Coordinator override; `none` disables. |
| `HYPERROUTE_NATIVE_TOOLS` | — | Exact coordinator tool ids. |
| `HYPERROUTE_HELD` | — | Comma-separated plan groups you hold. |

## License

MIT — see [LICENSE](LICENSE).
