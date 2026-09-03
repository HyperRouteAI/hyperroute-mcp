"""HyperRoute MCP — a Model Context Protocol server that exposes the HyperRoute router
(register, login, recommend, describe, onboard, execute, report_outcome, …) as MCP
tools, so a coordinator agent (Claude Code, Codex, Goose, Cursor, …) can drive the whole product
end-to-end in one conversation.

It talks to the router only over its public HTTP API and holds no product logic of its own.
"""

__version__ = "0.1.0"

from .server import mcp

__all__ = ["mcp", "__version__"]
