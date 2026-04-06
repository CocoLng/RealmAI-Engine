"""Entry point for python -m mcp_discord."""

from mcp_discord.server import mcp

mcp.run(transport="stdio")
