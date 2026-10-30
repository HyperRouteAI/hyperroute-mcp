# hyperroute-mcp

[Model Context Protocol](https://modelcontextprotocol.io) server for
[HyperRoute](https://hyperroute.io), a tool router for AI agents. A coordinator
agent (Claude Code, Codex, OpenCode, …) sends its outside lookups with `run`: each is ranked on its
own and the best tool runs on HyperRoute's server with the user's stored key. Items marked for it
get a score back.

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
| `run` | Lookups → each ranked on its own; clear picks run, results back; owed reports ride along. |
| `ranking` | The full ranking behind one run item (and the evidence, on request). |
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

A run reply looks like:

```
run r_7f3a
[1] frankfurter [settled, conf high; report if wrong]
  {"date":"2023-01-02","rates":{"JPY":130.92}}
[2] do it yourself
[3] needs a key: sonar_deep_research is best. STOP; ask the user (signup https://…)
  1. Create an account …
  then onboard('sonar_deep_research', <key>) and resend this item
owed: r_7f3a/4 → reports=[{item, score}] on next run
```

`HYPERROUTE_MCP_VERBS=classic` exposes `recommend` + `execute` instead (the web demo's agent uses it).

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
| `HYPERROUTE_MCP_VERBS` | `run` | `classic`: `recommend` + `execute` instead of `run` + `ranking`. |

## License

MIT — see [LICENSE](LICENSE).
