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

In Claude Code, type:

```
! uvx hyperroute-mcp@latest install
```

It adds HyperRoute to Claude Code and asks before changing anything else. Needs
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

- `finish_setup` shows what it would add for your agent (in Claude Code: hooks that make the agent and
  its subagents route through HyperRoute) and applies it only when you agree.
- `check_setup` checks versions and the installed pieces, and upgrades when you agree.
- `remove_setup` (or `hyperroute-mcp install --remove`) undoes it.

## Authenticate once

`recommend` and browsing are public — no account. Connecting keys and running tools need one.
Mint a personal access token at [hyperroute.io](https://hyperroute.io) and hand it to the
`use_token` tool; your password never enters the conversation. The login is cached on disk and
restored in every new session until the router invalidates it.

## Your coordinator

HyperRoute compares external tools against what your coordinator can already do. The server reads
which coordinator launched it and declares it for you; `session_info` shows what it resolved.

## Tools

| Tool | What it does |
|---|---|
| `recommend` | Task → ranked tools and how to act. Requires login. |
| `execute` | Run the chosen tool with your connected key; returns the result. |
| `connect_info` / `onboard` | A tool's signup steps; save and test its API key. |
| `report_outcome` / `report_narrative` | Feedback on a call, or on a whole run. |
| `describe` | One tool's details: `about` · `price` · `facets` · `evidence`. |
| `facets_catalog` / `get_preferences` / `set_preferences` | Preferences applied to every route. |
| `list_credentials` | Your connected tools (keys masked). |
| `fetch_result` | Page through a large result. |
| `console` | History, tools, keys, stats. |
| `my_tools` / `declare_my_tool` / `update_my_tool` / `remove_my_tool` / `my_tool_report` | Tools you already have, and when to use them. |
| `my_preferred_tools` / `prefer_tool` / `update_preferred_tool` / `unprefer_tool` | Catalog tools you favour. |
| `finish_setup` / `check_setup` / `remove_setup` | Setup, version checks and upgrades for your agent. |
| `session_info` / `health` | Connection and login state; router status. |
| `use_token` / `login_link` / `verify_login` / `login` / `register` / `verify` / `forgot_password` / `whoami` | Account. |

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
