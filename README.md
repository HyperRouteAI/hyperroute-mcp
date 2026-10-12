# hyperroute-mcp

[Model Context Protocol](https://modelcontextprotocol.io) server for
[HyperRoute](https://hyperroute.io), a tool router for AI agents. A coordinator
agent (Claude Code, Codex, OpenCode, …) calls `recommend` for an outside task, runs the pick with
`execute` (the tool runs on HyperRoute's server with the user's stored key), and reports the result
with `report_outcome`.

## Install

In your coding agent, type:

```
! uvx -q hyperroute-mcp@latest install
```

It adds HyperRoute to Claude Code with hooks: the routing rules at session and subagent start, a
guard that blocks the agent's own outside calls (web search and fetch, network commands, other MCP
servers) until HyperRoute has ranked the lookup, and an end-of-turn check for unreported runs.
`install --remind-only` makes the guard remind instead of block; `install --remove` undoes the
hooks. Needs [uv](https://docs.astral.sh/uv/).

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

HyperRoute compares external tools against what the coordinator can already do. The server reads
which coordinator launched it and declares it; `session_info` shows what it resolved.

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

`use`: `ready` (run it) · `needs_key` (connect a key first) · `native` (the agent does it itself, following the `instruction:` line) ·
`own` (your own tool) · `soon` (not runnable yet).

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

The suite is offline: the router is faked.

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
| `HYPERROUTE_GUARD` | block | `remind` makes the Claude Code guard remind instead of block. |

## License

MIT — see [LICENSE](LICENSE).
