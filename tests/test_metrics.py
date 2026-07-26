import os
import sys
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from metrics import total_kwh, comfort_violation_pct


def test_total_kwh_basic():
    df = pd.DataFrame({
        "sim_time_hours": [0, 0, 1, 1],
        "facility_electricity_j": [3.6e6, 3.6e6, 7.2e6, 7.2e6],
        "zone": ["A", "B", "A", "B"],
        "air_temp_c": [22, 22, 22, 22],
    })
    assert abs(total_kwh(df) - 2.0) < 1e-6


def test_comfort_violation_pct():
    df = pd.DataFrame({
        "air_temp_c": [21, 27, 22, 19],  # 2 of 4 out of [20,26] band
    })
    assert abs(comfort_violation_pct(df) - 50.0) < 1e-6
