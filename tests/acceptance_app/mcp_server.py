"""Minimal MCP Streamable HTTP server used by the acceptance suite."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "graphharbor-acceptance",
    instructions="Deterministic MCP fixture; never use this as a production service.",
    host="127.0.0.1",
    port=8765,
    streamable_http_path="/mcp",
    stateless_http=True,
)


@mcp.tool()
def project_fact(topic: str) -> str:
    """Return a deterministic project fact for MCP integration testing."""
    return f"{topic}: GraphHarbor uses PostgreSQL checkpoints and Redis transport."


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
