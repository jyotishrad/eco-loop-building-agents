"""
tools.py

Tool functions exposed to the LLM agent. These are the "hands" of the agent -
kept deliberately small and typed so the LLM can call them reliably via
JSON tool-calling. Same functions are reused whether you drive the agent
via plain Ollama tool-calling (llm_agent.py) or via the MCP server
(mcp_server.py) - single source of truth for the tool contract.

Constraint enforcement (comfort boundaries) lives here, NOT just in the
prompt - the agent may propose an unsafe setpoint, but this layer clamps it.
This matters directly for the "Thermal Comfort & Constraints" criterion:
whatever the LLM decides, the building physically cannot be pushed outside
the occupant comfort band.
"""

from dataclasses import dataclass

# Occupant comfort band (deg C) - adjust to your building's policy
COMFORT_COOLING_MIN = 22.5
COMFORT_COOLING_MAX = 26.0
COMFORT_HEATING_MIN = 19.0
COMFORT_HEATING_MAX = 21.5


@dataclass
class ToolContext:
    """Populated fresh each timestep by orchestrator.py before invoking the agent."""
    sim_time_hours: float
    outdoor_temp_c: float
    facility_electricity_j: float
    zones: dict  # {zone_name: {"air_temp_c": .., "cooling_setpoint_c": .., "heating_setpoint_c": ..}}
    grid_carbon_intensity: float = 0.4  # kg CO2/kWh, placeholder - swap for a real grid API if desired


def get_building_state(ctx: ToolContext) -> dict:
    """Tool: return current sensor readings for every zone plus outdoor conditions."""
    return {
        "sim_time_hours": ctx.sim_time_hours,
        "outdoor_temp_c": round(ctx.outdoor_temp_c, 2),
        "facility_electricity_kwh_cumulative": round(ctx.facility_electricity_j / 3.6e6, 3),
        "grid_carbon_intensity_kg_per_kwh": ctx.grid_carbon_intensity,
        "zones": ctx.zones,
    }


def set_zone_setpoints(zone: str, cooling_setpoint_c: float = None,
                        heating_setpoint_c: float = None) -> dict:
    """
    Tool: propose new setpoints for a zone. Values are clamped to the comfort
    band before being handed back to EnergyPlus - the agent can push toward
    the edges of the band to save energy, but never outside it.
    """
    result = {"zone": zone, "applied": {}, "clamped": False}

    if cooling_setpoint_c is not None:
        clamped = min(max(cooling_setpoint_c, COMFORT_COOLING_MIN), COMFORT_COOLING_MAX)
        if clamped != cooling_setpoint_c:
            result["clamped"] = True
        result["applied"]["cooling_setpoint_c"] = clamped

    if heating_setpoint_c is not None:
        clamped = min(max(heating_setpoint_c, COMFORT_HEATING_MIN), COMFORT_HEATING_MAX)
        if clamped != heating_setpoint_c:
            result["clamped"] = True
        result["applied"]["heating_setpoint_c"] = clamped

    return result


def get_comfort_policy() -> dict:
    """Tool: let the agent introspect the current comfort constraints instead of hardcoding them in the prompt."""
    return {
        "cooling_setpoint_range_c": [COMFORT_COOLING_MIN, COMFORT_COOLING_MAX],
        "heating_setpoint_range_c": [COMFORT_HEATING_MIN, COMFORT_HEATING_MAX],
    }


# --- JSON schema definitions for Ollama / MCP tool-calling ---
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_building_state",
            "description": "Get current sensor readings for all zones and outdoor conditions.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_comfort_policy",
            "description": "Get the allowed cooling/heating setpoint ranges (comfort constraints).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_zone_setpoints",
            "description": "Propose new cooling and/or heating setpoints (Celsius) for one zone.",
            "parameters": {
                "type": "object",
                "properties": {
                    "zone": {"type": "string"},
                    "cooling_setpoint_c": {"type": "number"},
                    "heating_setpoint_c": {"type": "number"},
                },
                "required": ["zone"],
            },
        },
    },
]
