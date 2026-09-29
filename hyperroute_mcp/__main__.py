import sys
import threading


def main() -> None:
    from .cli import main as cli_main
    code = cli_main(sys.argv[1:])
    if code is not None:
        sys.exit(code)
    from . import agents, install
    try:
        agents.repair_all()
    except Exception:
        pass
    threading.Thread(target=install.latest, daemon=True).start()
    from .server import mcp
    mcp.run()


if __name__ == "__main__":
    main()
