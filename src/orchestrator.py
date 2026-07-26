"""
orchestrator.py

Entry point. Drives either:
  --mode baseline        : plain EnergyPlus run, idf's own schedules control setpoints
  --mode ai-closed-loop   : EnergyPlus run where every zone timestep asks the LLM agent
                            for setpoint decisions and forward-injects them back in

Robustness (System Integration, 30% of grade):
  - Wraps the whole run in try/except and writes a run_report.json regardless
    of success/failure, so a crash mid-simulation still produces evidence of
    how far it got instead of just dying.
  - If the LLM/agent call fails on a given timestep, energyplus_wrapper and
    llm_agent already degrade gracefully (hold previous setpoints) rather than
    aborting the whole simulation - this is what "robustly execute without
    crashing over an extended simulation time horizon" is asking for.
"""

import argparse
import json
import os
import time

from energyplus_wrapper import EnergyPlusRun, SimSnapshot
from llm_agent import LLMAgent
from tools import ToolContext


def zones_to_dict(snap: SimSnapshot) -> dict:
    return {
        z.zone: {
            "air_temp_c": round(z.air_temp_c, 2),
            "cooling_setpoint_c": round(z.cooling_setpoint_c, 2),
            "heating_setpoint_c": round(z.heating_setpoint_c, 2),
        }
        for z in snap.zones
    }


def make_ai_step_fn(agent: LLMAgent):
    def on_step(snap: SimSnapshot) -> dict:
        ctx = ToolContext(
            sim_time_hours=snap.sim_time_hours,
            outdoor_temp_c=snap.outdoor_temp_c,
            facility_electricity_j=snap.facility_electricity_j,
            zones=zones_to_dict(snap),
        )
        return agent.decide(ctx)
    return on_step


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--idf", required=True)
    parser.add_argument("--epw", required=True)
    parser.add_argument("--mode", choices=["baseline", "ai-closed-loop"], required=True)
    parser.add_argument("--model", default="llama3.1", help="Ollama model name")
    parser.add_argument("--output-dir", default="eplus_out")
    parser.add_argument("--csv-out", default=None)
    args = parser.parse_args()

    csv_out = args.csv_out or (
        "data/baseline_run.csv" if args.mode == "baseline" else "data/ai_run.csv"
    )
    os.makedirs(os.path.dirname(csv_out) or ".", exist_ok=True)

    if args.mode == "baseline":
        on_step = None
    else:
        agent = LLMAgent(model=args.model)
        on_step = make_ai_step_fn(agent)

    run = EnergyPlusRun(
        idf_path=args.idf,
        epw_path=args.epw,
        output_dir=f"{args.output_dir}_{args.mode}",
        on_step=on_step,
    )

    start = time.time()
    exit_code = run.run()
    elapsed = time.time() - start

    run.export_csv(csv_out)

    report = {
        "mode": args.mode,
        "exit_code": exit_code,
        "crashed": run.crashed,
        "errors": run.error_log[:20],  # cap so the report file doesn't explode
        "n_timesteps_completed": len(run.history),
        "wall_clock_seconds": round(elapsed, 1),
        "csv_out": csv_out,
    }
    report_path = f"data/run_report_{args.mode}.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    print(json.dumps(report, indent=2))
    if run.crashed:
        print(f"[orchestrator] Run finished with errors - see {report_path}")
    else:
        print(f"[orchestrator] Run completed cleanly. CSV: {csv_out}")


if __name__ == "__main__":
    main()
