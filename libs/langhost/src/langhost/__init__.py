"""GraphHarbor CLI for a self-hosted LangGraph Agent Server."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("graphharbor")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"

__all__ = ["__version__"]
