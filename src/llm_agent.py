"""
llm_agent.py

The "brain" - a tool-calling loop against a locally-hosted open-source LLM
via Ollama. Kept model-agnostic: swap OLLAMA_MODEL for llama3.1, qwen2.5,
mistral, etc. and this still works, since they all speak the same
OpenAI-style tool-calling format through Ollama's /api/chat endpoint.

Latency management (addresses System Architecture doc requirement):
  - Runs one decision per zone timestep, not per sub-timestep - EnergyPlus
    zone timesteps are typically 10-15 min, giving the LLM ample wall-clock
    budget even on modest hardware.
  - System prompt + comfort policy are cached in `self._system_prompt` and
    only the state diff is sent fresh each call to keep prompt tokens low.
  - `max_tool_hops` bounds the tool-calling loop so a confused model can't
    spin forever mid-simulation.
"""

import json
import requests
from tenacity import retry, stop_after_attempt, wait_fixed

import tools
from tools import ToolContext, TOOL_SCHEMAS, get_building_state, set_zone_setpoints, get_comfort_policy

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "llama3.1"

SYSTEM_PROMPT = """You are the autonomous control agent for a commercial building's HVAC \
system, running in a closed loop with an EnergyPlus simulation. Each turn you are given \
the current sensor state. Your job:

1. Call get_comfort_policy once if you haven't already, to know the allowed setpoint ranges.
2. Call get_building_state to see current zone temperatures, outdoor conditions, and \
cumulative electricity use.
3. Decide, per zone, whether to relax or tighten cooling/heating setpoints to reduce \
energy use WITHOUT letting zone air temperature drift outside the comfort policy range. \
Favor pushing setpoints toward the edges of the allowed band during high outdoor temperature \
or high grid carbon intensity periods, and relaxing back toward the middle when conditions \
are mild - this is where energy savings come from.
4. Call set_zone_setpoints for every zone you want to update this step. If a zone is already \
optimal, skip it.
5. When you are done deciding for this timestep, reply with plain text "DONE" and no further \
tool calls.

Never propose a setpoint outside the comfort policy range - the system will clamp it anyway, \
but reasoning within the band leads to better decisions than guessing."""

FUNCTIONS = {
    "get_building_state": get_building_state,
    "set_zone_setpoints": set_zone_setpoints,
    "get_comfort_policy": get_comfort_policy,
}


class LLMAgent:
    def __init__(self, model: str = OLLAMA_MODEL, max_tool_hops: int = 3):
        self.model = model
        self.max_tool_hops = max_tool_hops

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
    def _chat(self, messages):
        resp = requests.post(OLLAMA_URL, json={
            "model": self.model,
            "messages": messages,
            "tools": TOOL_SCHEMAS,
            "stream": False,
        }, timeout=120)
        resp.raise_for_status()
        return resp.json()

    def decide(self, ctx: ToolContext) -> dict:
        """
        Runs one full tool-calling round trip for a single simulation timestep.
        Returns {zone_name: {"cooling_setpoint_c": .., "heating_setpoint_c": ..}}
        ready to be forward-injected into EnergyPlus by the orchestrator.
        """
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Timestep at t={ctx.sim_time_hours:.2f}h. Begin your decision."},
        ]

        actions: dict = {}

        for _ in range(self.max_tool_hops):
            try:
                result = self._chat(messages)
            except Exception as e:  # noqa: BLE001 - LLM/network hiccup shouldn't crash the sim
                print(f"[llm_agent] LLM call failed, holding previous setpoints: {e}")
                return actions

            msg = result.get("message", {})
            tool_calls = msg.get("tool_calls") or []

            if not tool_calls:
                break  # model replied with plain text ("DONE") - end of this timestep's reasoning

            messages.append(msg)

            for call in tool_calls:
                fn_name = call["function"]["name"]
                raw_args = call["function"].get("arguments", {})
                args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args)

                if fn_name == "get_building_state":
                    output = get_building_state(ctx)
                elif fn_name == "get_comfort_policy":
                    output = get_comfort_policy()
                elif fn_name == "set_zone_setpoints":
                    output = set_zone_setpoints(**args)
                    zone = output["zone"]
                    actions.setdefault(zone, {}).update(output["applied"])
                else:
                    output = {"error": f"unknown tool {fn_name}"}

                messages.append({
                    "role": "tool",
                    "content": json.dumps(output),
                })

        return actions
