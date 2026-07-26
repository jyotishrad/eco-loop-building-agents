# System Architecture — Eco-Loop Building Agents

## 1. Overview

The system closes the loop between a physics-based building simulation
(EnergyPlus) and an open-source LLM control agent, using EnergyPlus's
native EMS (Energy Management System) Python callback API as the real-time
bridge — not file-based batch runs. Every zone timestep, the simulation
pauses inside a Python callback, hands sensor data to the agent, and applies
whatever setpoint decision comes back before advancing.

```
┌──────────────┐   sensor readings    ┌───────────────┐   tool calls   ┌────────────┐
│  EnergyPlus  │ ───────────────────► │  Orchestrator  │ ─────────────► │ LLM Agent  │
│ (EMS runtime)│                      │ (Python glue)  │                │  (Ollama)  │
│              │ ◄─────────────────── │                │ ◄───────────── │            │
└──────────────┘  actuator setpoints  └───────────────┘  tool results   └────────────┘
```

## 2. Tool-calling architecture

Tools live in `src/tools.py` as plain Python functions with JSON schemas
(`TOOL_SCHEMAS`), reused by two front-ends:

- `src/llm_agent.py` — talks directly to Ollama's `/api/chat` endpoint using
  its native OpenAI-style tool-calling format. This is the default path
  used by `orchestrator.py`.
- `src/mcp_server.py` — the same tools exposed over a real MCP stdio server
  (via `mcp.server.fastmcp.FastMCP`), for interoperability with any
  MCP-compatible client/host.

Three tools are exposed: `get_building_state`, `get_comfort_policy`,
`set_zone_setpoints`. Keeping the tool surface small and single-purpose
was a deliberate choice — it reduces the chance of the LLM hallucinating
malformed calls and keeps each round trip fast.

Constraint enforcement is done in the tool layer (`set_zone_setpoints`
clamps to the comfort band), not just via prompting. This means the
system's safety property (never leave the comfort band) holds even if the
LLM proposes something unsafe — the prompt asks it to reason well, the
code guarantees it can't do damage either way.

## 3. Prompt engineering strategy

- **System prompt is static and cached** (see `SYSTEM_PROMPT` in
  `llm_agent.py`) — it's sent once per timestep's conversation, not
  regenerated, keeping token overhead predictable.
- **State is delivered via tool calls, not stuffed into the prompt.** The
  agent explicitly calls `get_building_state` rather than receiving a wall
  of sensor data every turn. This lets the agent skip a call when it
  already has fresh-enough context within the same reasoning chain, and
  keeps prompts short on simple timesteps.
- **Explicit termination signal** — the agent is instructed to reply
  `"DONE"` once it's finished deciding for the timestep, giving a clean
  exit condition for the tool-calling loop instead of relying on timeout.
- **Bounded reasoning loop** — `max_tool_hops` (default 6) caps how many
  tool calls the agent can make per timestep, preventing a confused model
  from stalling the simulation indefinitely.

## 4. Latency management

EnergyPlus zone timesteps default to 10–15 minutes of simulated time, so
even a multi-second LLM round trip per timestep is negligible against total
simulation wall-clock time for a day-long or week-long run. Concretely:

- One LLM decision cycle per **zone timestep**, not per sub-timestep or HVAC
  iteration — avoids redundant calls during EnergyPlus's internal system
  timestep convergence loop.
- Tool responses are minimal JSON (rounded floats, no extraneous fields) to
  keep both directions of each round trip small.
- `tenacity`-based retry (3 attempts, fixed 1s backoff) on the Ollama call
  absorbs transient local-server hiccups without escalating to a full
  simulation abort.

## 5. Handling lengthy simulation logs

- EnergyPlus's own verbose output (`eplusout.err`, `.eso`, etc.) is left in
  its per-run output directory (`eplus_out_<mode>/`) rather than parsed
  live — only the specific EMS variables/actuators we registered handles
  for are read each timestep, so the agent never has to ingest raw log
  text.
- Our own per-timestep telemetry is streamed straight to a `SimSnapshot`
  dataclass held in memory and only flushed to CSV (`data/*_run.csv`) once
  at the end of the run via `EnergyPlusRun.export_csv()`, avoiding repeated
  disk I/O every timestep.
- `run_report_<mode>.json` caps stored error strings at 20 entries so a
  pathological run that errors every timestep still produces a small,
  readable report instead of a multi-MB log dump.

## 6. Fault tolerance (System Integration criterion)

- All EnergyPlus EMS callback code is wrapped in try/except; on any
  exception the run is flagged `crashed=True` and the error is recorded,
  but the callback returns cleanly so EnergyPlus itself keeps running
  rather than crashing the whole process.
- If the LLM/network call fails, `llm_agent.decide()` catches the exception
  and returns whatever setpoint actions were already decided that hop
  (or none) — the simulation holds the previous setpoints and continues
  rather than stalling.
- `orchestrator.py` always writes `run_report_<mode>.json` in a `finally`-
  equivalent path (after `run.run()` returns, success or not), so there is
  always evidence of run duration and failure point even for a crashed run.

## 7. Baseline vs. AI-controlled comparison methodology

Two independent EnergyPlus runs are executed against the **same** `.idf`
and `.epw`:

- `--mode baseline`: no `on_step` callback is registered for actuator
  writes, so the building runs under its own idf-defined thermostat
  schedules — this is the control condition.
- `--mode ai-closed-loop`: the LLM agent's `decide()` output is
  forward-injected as actuator writes every zone timestep.

`src/metrics.py` then computes % kWh reduction and comfort-band violation
percentage for both runs from their exported CSVs, surfaced in
`dashboard/app.py`.
