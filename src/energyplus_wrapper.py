"""
energyplus_wrapper.py

Wraps the EnergyPlus Python API so the rest of the system can:
  - run a simulation with a callback fired every zone timestep
  - read live sensor data (zone temp, humidity, energy meters)
  - write actuator setpoints back in the same timestep (the "forward injection")

Requires the EnergyPlus install directory on PYTHONPATH, e.g.:
    export PYTHONPATH=/usr/local/EnergyPlus-24-1-0:$PYTHONPATH
"""

import os
import sys
import csv
from dataclasses import dataclass, field
from typing import Callable, Optional

ENERGYPLUS_DIR = os.environ.get("ENERGYPLUS_DIR", "")
if ENERGYPLUS_DIR and ENERGYPLUS_DIR not in sys.path:
    sys.path.insert(0, ENERGYPLUS_DIR)

try:
    from pyenergyplus.api import EnergyPlusAPI
except ImportError as e:
    raise ImportError(
        "Could not import pyenergyplus. Set ENERGYPLUS_DIR to your EnergyPlus "
        "install path (the folder containing the pyenergyplus package). "
        f"Original error: {e}"
    )

ZONE_LIST = ["CORE_ZN", "PERIMETER_ZN_1", "PERIMETER_ZN_2", "PERIMETER_ZN_3", "PERIMETER_ZN_4"]


@dataclass
class ZoneSnapshot:
    zone: str
    air_temp_c: float
    cooling_setpoint_c: float
    heating_setpoint_c: float


@dataclass
class SimSnapshot:
    sim_time_hours: float
    zones: list = field(default_factory=list)
    facility_electricity_j: float = 0.0
    outdoor_temp_c: float = 0.0
    crashed: bool = False


class EnergyPlusRun:
    """One simulation run (baseline OR ai-controlled). Create a fresh instance per run."""

    def __init__(self, idf_path: str, epw_path: str, output_dir: str,
                 on_step: Optional[Callable[[SimSnapshot], dict]] = None):
        self.idf_path = idf_path
        self.epw_path = epw_path
        self.output_dir = output_dir
        self.on_step = on_step or (lambda snap: {})
        self.api = EnergyPlusAPI()
        self.state = self.api.state_manager.new_state()
        self._handles_resolved = False
        self._sensor_handles = {}
        self._actuator_handles = {}
        self.crashed = False
        self.error_log = []
        self.history: list[SimSnapshot] = []

    def _resolve_handles(self, state):
        exch = self.api.exchange
        for zone in ZONE_LIST:
            self._sensor_handles[zone] = {
                "temp": exch.get_variable_handle(state, "Zone Mean Air Temperature", zone),
            }
            self._actuator_handles[zone] = {
                "cooling": exch.get_actuator_handle(
                    state, "Zone Temperature Control", "Cooling Setpoint", zone),
                "heating": exch.get_actuator_handle(
                    state, "Zone Temperature Control", "Heating Setpoint", zone),
            }
        self._facility_elec_handle = exch.get_meter_handle(state, "Electricity:Facility")
        self._oat_handle = exch.get_variable_handle(
            state, "Site Outdoor Air Drybulb Temperature", "Environment")
        self._handles_resolved = True

    def _callback(self, state):
        exch = self.api.exchange
        try:
            if exch.warmup_flag(state):
                return
            if exch.kind_of_sim(state) != 3:
                return
            if not self._handles_resolved:
                self._resolve_handles(state)

            zones = []
            for zone in ZONE_LIST:
                h = self._sensor_handles[zone]
                ah = self._actuator_handles[zone]
                zones.append(ZoneSnapshot(
                    zone=zone,
                    air_temp_c=exch.get_variable_value(state, h["temp"]),
                    cooling_setpoint_c=exch.get_actuator_value(state, ah["cooling"]),
                    heating_setpoint_c=exch.get_actuator_value(state, ah["heating"]),
                ))

            snap = SimSnapshot(
                sim_time_hours=exch.current_sim_time(state),
                zones=zones,
                facility_electricity_j=exch.get_meter_value(state, self._facility_elec_handle),
                outdoor_temp_c=exch.get_variable_value(state, self._oat_handle),
            )
            self.history.append(snap)

            actions = self.on_step(snap) or {}
            for zone, setpoints in actions.items():
                ah = self._actuator_handles.get(zone)
                if not ah:
                    continue
                if "cooling_setpoint_c" in setpoints:
                    exch.set_actuator_value(state, ah["cooling"], setpoints["cooling_setpoint_c"])
                if "heating_setpoint_c" in setpoints:
                    exch.set_actuator_value(state, ah["heating"], setpoints["heating_setpoint_c"])

        except Exception as e:
            self.crashed = True
            self.error_log.append(str(e))

    def run(self):
        self.api.runtime.callback_end_zone_timestep_after_zone_reporting(self.state, self._callback)
        os.makedirs(self.output_dir, exist_ok=True)
        argv = [
            "-w", self.epw_path,
            "-d", self.output_dir,
            "-r",
            self.idf_path,
        ]
        try:
            exit_code = self.api.runtime.run_energyplus(self.state, argv)
        except Exception as e:
            self.crashed = True
            self.error_log.append(f"Fatal runtime error: {e}")
            exit_code = -1
        if exit_code != 0:
            self.crashed = True
        return exit_code

    def export_csv(self, path: str):
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["sim_time_hours", "zone", "air_temp_c", "cooling_setpoint_c",
                              "heating_setpoint_c", "facility_electricity_j", "outdoor_temp_c"])
            for snap in self.history:
                for z in snap.zones:
                    writer.writerow([snap.sim_time_hours, z.zone, z.air_temp_c,
                                      z.cooling_setpoint_c, z.heating_setpoint_c,
                                      snap.facility_electricity_j, snap.outdoor_temp_c])
