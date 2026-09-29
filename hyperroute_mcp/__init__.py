__version__ = "0.6.0"

__all__ = ["mcp", "__version__"]


def __getattr__(name):
    if name == "mcp":
        from .server import mcp
        return mcp
    raise AttributeError(name)
