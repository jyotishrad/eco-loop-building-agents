"""
dashboard/app.py

Deliverable #3: Quantitative Savings Dashboard.
Run with: streamlit run dashboard/app.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from metrics import load_run, total_kwh, comfort_violation_pct, summarize

st.set_page_config(page_title="Eco-Loop Savings Dashboard", layout="wide")
st.title("Eco-Loop Building Agents — Quantitative Savings Dashboard")

baseline_path = st.sidebar.text_input("Baseline CSV", "data/baseline_run.csv")
ai_path = st.sidebar.text_input("AI closed-loop CSV", "data/ai_run.csv")

if not (os.path.exists(baseline_path) and os.path.exists(ai_path)):
    st.warning(
        "Run both simulations first:\n\n"
        "```\npython src/orchestrator.py --idf models/baseline.idf --epw <weather.epw> --mode baseline\n"
        "python src/orchestrator.py --idf models/baseline.idf --epw <weather.epw> --mode ai-closed-loop\n```"
    )
    st.stop()

base_df = load_run(baseline_path)
ai_df = load_run(ai_path)
summary = summarize(baseline_path, ai_path)

col1, col2, col3 = st.columns(3)
col1.metric("Baseline energy use", f"{summary['baseline_kwh']:.1f} kWh")
col2.metric("AI closed-loop energy use", f"{summary['ai_kwh']:.1f} kWh",
            delta=f"-{summary['pct_energy_reduction']:.1f}%")
col3.metric("Energy reduction", f"{summary['pct_energy_reduction']:.1f}%")

st.subheader("Cumulative Facility Electricity (kWh)")
base_ts = base_df.groupby("sim_time_hours")["facility_electricity_j"].max() / 3.6e6
ai_ts = ai_df.groupby("sim_time_hours")["facility_electricity_j"].max() / 3.6e6

fig = go.Figure()
fig.add_trace(go.Scatter(x=base_ts.index, y=base_ts.values, name="Baseline"))
fig.add_trace(go.Scatter(x=ai_ts.index, y=ai_ts.values, name="AI closed-loop"))
fig.update_layout(xaxis_title="Simulation time (h)", yaxis_title="Cumulative kWh")
st.plotly_chart(fig, use_container_width=True)

st.subheader("Thermal Comfort — Zone Temperatures")
col1, col2 = st.columns(2)
with col1:
    st.caption(f"Baseline out-of-comfort-band: {summary['baseline_comfort_violation_pct']:.2f}% of timesteps")
    st.plotly_chart(px.line(base_df, x="sim_time_hours", y="air_temp_c", color="zone",
                             title="Baseline"), use_container_width=True)
with col2:
    st.caption(f"AI closed-loop out-of-comfort-band: {summary['ai_comfort_violation_pct']:.2f}% of timesteps")
    st.plotly_chart(px.line(ai_df, x="sim_time_hours", y="air_temp_c", color="zone",
                             title="AI closed-loop"), use_container_width=True)

st.subheader("Setpoint Trajectories (AI closed-loop)")
st.plotly_chart(
    px.line(ai_df, x="sim_time_hours", y="cooling_setpoint_c", color="zone",
            title="Cooling setpoints chosen by the LLM agent over time"),
    use_container_width=True,
)

st.caption(
    "Comfort band evaluated for reporting: 20-26°C occupant-perceived range "
    "(configurable in src/metrics.py). Agent optimizes within a tighter band "
    "defined in src/tools.py."
)
