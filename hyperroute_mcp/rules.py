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
  to find it), never settle for a snippet. `needs a key` STOP that item:
  tell the user "<tool> is best for this and needs a key", show the steps, ask for the key; do not do
  it another way. `no tool` / `no tested tool` do it yourself or pick a listed tool; tell the user in
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


def text(server: str = "hyperroute") -> str:
    return _RULES.format(p=f"mcp__{server}__", guard=_GUARD)


def plain(guard: bool = True) -> str:
    """The same rules naming the tools without a client-specific prefix."""
    return _RULES.format(p="", guard=_GUARD if guard else "")
