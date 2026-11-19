from __future__ import annotations

# The routing rules every agent receives: at session start and subagent start (Claude Code hooks),
# at the head of the server instructions, and in the instruction file other agents always load.
# The agent sends lookups with `run` and HyperRoute acts on each ranking itself, so the rules say how
# to read an item, not how to carry out a ranking.
_RULES = """# HyperRoute: hard rules (breaking one = CRITICAL FAILURE)

- Outside lookup = web search, page fetch, third-party API, remote MCP; by any route (tool, curl,
  script). Send each via `{p}run`: one item per lookup, that lookup alone in its query. Batch every
  lookup you already need into ONE call. Never two lookups in one item.
{guard}- Not outside (no `run`): the user's files, git with their remotes, package installs, their storage,
  your task list, local MCP servers, the user's connected accounts.
- Per item: `ran` use the result. `do it yourself` use your own tools; its brief sets depth, not
  method: when an official API, registry or the page itself holds the answer, fetch it (search only
  to find it), never settle for a snippet. A `better:` line (a stronger tool needs a key): carry on
  with what the item says, and tell the user in one line which tool is better (and by how much, if
  the line gives a gap).
  `needs a key` (nothing you have can do it): tell the user "<tool> is best for this and needs a
  key", show the steps, ask for the key. `no tool` / `no tested tool` do it yourself or pick a listed tool; tell the user in
  one line.
- "report this one": send a score in `reports` on your next `run`, or `{p}report_outcome(item=…)`
  before finishing. "report only if wrong": report only then. Reports are silent. A wrong or empty
  result: report it `useless` (`blocked` if it errored); the reply brings the next tool's result or
  hands the item to you ("for r_x/n"). Never resend a lookup a tool just got wrong.
- Never put the user's email, name, account or token in an outside request.
- State only what a tool result showed; mark the rest "not verified".

Why a pick, other candidates, evidence: `{p}ranking(run, n)`. Run another row: resend the item with `tool`."""


# Only where the guard hook is installed: on a hosted client nothing checks the agent's own calls.
_GUARD = """- Or call your own tool directly: HyperRoute checks it first, allows it when yours is best, else
  stops it. Stopped: send that lookup via `run`. Never route around a stop.
"""


# Lite mode (HYPERROUTE_LITE=1): about 100 tokens and three tools, for agents that want HyperRoute's
# routing and nothing else on their path. The full rules above stay the default.
_LITE = """HyperRoute: send each outside lookup (web search, page, API) as one item to `{p}run`; batch
them in one call. `ran`: use the result. `do it yourself`: use your own tools, as deep as its brief
says. A page that refused you on such an item: report it `blocked` in your next run's `reports`.
`needs a key`: tell the user. A `better:` line: tell the user in one line. Never put the user's details
in a query."""


def lite() -> bool:
    import os
    return os.environ.get("HYPERROUTE_LITE", "").strip() in ("1", "true", "yes")


def text(server: str = "hyperroute") -> str:
    if lite():
        return _LITE.format(p=f"mcp__{server}__")
    return _RULES.format(p=f"mcp__{server}__", guard=_GUARD)


def plain(guard: bool = True) -> str:
    """The same rules naming the tools without a client-specific prefix."""
    if lite():
        return _LITE.format(p="")
    return _RULES.format(p="", guard=_GUARD if guard else "")
