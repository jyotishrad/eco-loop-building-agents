"""
mcp_server.py

Optional: exposes the same tool functions from tools.py over an actual MCP
(Model Context Protocol) stdio server, for the "leverage of MCP protocols"
part of the Agentic Autonomy criterion. This is a thin adapter - the tool
logic itself still lives in tools.py, so behavior is identical whether you
drive the agent through llm_agent.py's direct Ollama tool-calling or through
this MCP server with an MCP-compatible client.

Run standalone to sanity-check it:
    python src/mcp_server.py

The orchestrator does not require this file to function - it's an additive
demonstration of MCP support layered on top of the same tool contract.
"""

import json
from mcp.server.fastmcp import FastMCP

import tools
from tools import ToolContext

mcp = FastMCP("eco-loop-building-agents")

# Orchestrator updates this before each agent turn when running in MCP mode.
_current_ctx: ToolContext | None = None


def set_context(ctx: ToolContext):
    global _current_ctx
    _current_ctx = ctx


@mcp.tool()
def get_building_state() -> str:
    """Get current sensor readings for all zones and outdoor conditions."""
    if _current_ctx is None:
        return json.dumps({"error": "no active simulation context"})
    return json.dumps(tools.get_building_state(_current_ctx))


@mcp.tool()
def get_comfort_policy() -> str:
    """Get the allowed cooling/heating setpoint ranges (comfort constraints)."""
    return json.dumps(tools.get_comfort_policy())


@mcp.tool()
def set_zone_setpoints(zone: str, cooling_setpoint_c: float = None,
                        heating_setpoint_c: float = None) -> str:
    """Propose new cooling/heating setpoints (Celsius) for one zone."""
    return json.dumps(tools.set_zone_setpoints(zone, cooling_setpoint_c, heating_setpoint_c))


if __name__ == "__main__":
    mcp.run(transport="stdio")
