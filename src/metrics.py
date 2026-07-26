"""
metrics.py

Turns raw baseline_run.csv / ai_run.csv exports into the numbers the
evaluation criteria actually ask for:
  - % reduction in total kWh (Energy Efficiency Realized, 25%)
  - whether comfort boundaries were held (Thermal Comfort & Constraints, 20%)

Used by both dashboard/app.py (visual) and can be run standalone for a
plain-text summary to paste into the System Architecture doc or slides.
"""

import argparse
import pandas as pd

COMFORT_MIN_C = 20.0  # occupant-perceived comfort band for REPORTING purposes
COMFORT_MAX_C = 26.0  # (separate from the tighter setpoint band the agent optimizes within)


def load_run(csv_path: str) -> pd.DataFrame:
    return pd.read_csv(csv_path)


def total_kwh(df: pd.DataFrame) -> float:
    # facility_electricity_j is cumulative per the EnergyPlus meter; take max per zone-agnostic timeline
    per_step = df.groupby("sim_time_hours")["facility_electricity_j"].max()
    return per_step.iloc[-1] / 3.6e6 if len(per_step) else 0.0


def comfort_violation_pct(df: pd.DataFrame) -> float:
    out_of_band = (df["air_temp_c"] < COMFORT_MIN_C) | (df["air_temp_c"] > COMFORT_MAX_C)
    return 100.0 * out_of_band.mean() if len(df) else 0.0


def summarize(baseline_csv: str, ai_csv: str) -> dict:
    base_df = load_run(baseline_csv)
    ai_df = load_run(ai_csv)

    base_kwh = total_kwh(base_df)
    ai_kwh = total_kwh(ai_df)
    pct_reduction = 100.0 * (base_kwh - ai_kwh) / base_kwh if base_kwh else 0.0

    return {
        "baseline_kwh": round(base_kwh, 2),
        "ai_kwh": round(ai_kwh, 2),
        "pct_energy_reduction": round(pct_reduction, 2),
        "baseline_comfort_violation_pct": round(comfort_violation_pct(base_df), 2),
        "ai_comfort_violation_pct": round(comfort_violation_pct(ai_df), 2),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", default="data/baseline_run.csv")
    parser.add_argument("--ai", default="data/ai_run.csv")
    args = parser.parse_args()

    result = summarize(args.baseline, args.ai)
    for k, v in result.items():
        print(f"{k}: {v}")
