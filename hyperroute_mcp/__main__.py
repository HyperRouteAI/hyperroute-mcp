"""`python -m hyperroute_mcp` — run the HyperRoute MCP server over stdio (the transport MCP
clients launch it with). Point it at a router with HYPERROUTE_BASE_URL; see config.py for the
full set of environment variables."""

from .server import mcp


def main() -> None:
    mcp.run()  # stdio transport by default


if __name__ == "__main__":
    main()
