import csv
import json
import os
import requests
from dataclasses import dataclass, asdict
from typing import Dict, Any, List, Optional, Callable
from pyenergyplus.api import EnergyPlusAPI


@dataclass
class SimSnapshot:
    timestep: int
    sim_time_hours: float
    outdoor_temp_c: float
    zone_temp_c: float
    heating_setpoint: float
    cooling_setpoint: float
    facility_elec_w: float


class EnergyPlusRun:
    def __init__(
        self,
        idf_path: str,
        epw_path: str,
        output_dir: str,
        mode: str = "baseline",
        model_name: str = "qwen2.5:3b",
        ollama_url: str = "http://localhost:11434/api/generate",
        on_step: Optional[Callable[[SimSnapshot], None]] = None,
    ):
        self.idf_path = idf_path
        self.epw_path = epw_path
        self.output_dir = output_dir
        self.mode = mode
        self.model_name = model_name
        self.ollama_url = ollama_url
        self.on_step = on_step

        self.api = EnergyPlusAPI()
        self.state = self.api.state_manager.new_state()

        self._handles_resolved = False
        self._zone_temp_handle = -1
        self._htg_sp_handle = -1
        self._clg_sp_handle = -1
        self._facility_elec_handle = -1
        self._oat_handle = -1

        self._htg_actuator = -1
        self._clg_actuator = -1

        self.history: List[SimSnapshot] = []
        self.snapshots: List[SimSnapshot] = self.history
        self.n_timesteps = 0
        self.crashed = False
        self.error_log: List[str] = []

    def _resolve_handles(self, state):
        exch = self.api.exchange
        self._zone_temp_handle = exch.get_variable_handle(
            state, "Zone Mean Air Temperature", "CORE_ZN"
        )
        self._htg_sp_handle = exch.get_variable_handle(
            state, "Zone Thermostat Heating Setpoint Temperature", "CORE_ZN"
        )
        self._clg_sp_handle = exch.get_variable_handle(
            state, "Zone Thermostat Cooling Setpoint Temperature", "CORE_ZN"
        )
        self._facility_elec_handle = exch.get_meter_handle(
            state, "Electricity:Facility"
        )
        self._oat_handle = exch.get_variable_handle(
            state, "Site Outdoor Air Drybulb Temperature", "Environment"
        )

        self._htg_actuator = exch.get_actuator_handle(
            state, "Zone Temperature Control", "Heating Setpoint", "CORE_ZN"
        )
        self._clg_actuator = exch.get_actuator_handle(
            state, "Zone Temperature Control", "Cooling Setpoint", "CORE_ZN"
        )
        self._handles_resolved = True

    def _query_llm(self, snapshot: SimSnapshot) -> Dict[str, float]:
        prompt = (
            f"Building Control Step {snapshot.timestep}:\n"
            f"Outdoor Temp: {snapshot.outdoor_temp_c:.1f}C, Zone Temp: {snapshot.zone_temp_c:.1f}C.\n"
            f"Current Setpoints: Heating={snapshot.heating_setpoint:.1f}C, Cooling={snapshot.cooling_setpoint:.1f}C.\n"
            f"Facility Power: {snapshot.facility_elec_w:.1f}W.\n"
            f"Respond strictly in JSON with keys 'heating_setpoint' and 'cooling_setpoint'."
        )
        try:
            res = requests.post(
                self.ollama_url,
                json={
                    "model": self.model_name,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                },
                timeout=5,
            )
            if res.status_code == 200:
                data = json.loads(res.json().get("response", "{}"))
                return {
                    "heating_setpoint": float(data.get("heating_setpoint", snapshot.heating_setpoint)),
                    "cooling_setpoint": float(data.get("cooling_setpoint", snapshot.cooling_setpoint)),
                }
        except Exception as e:
            self.error_log.append(f"LLM call failed at step {snapshot.timestep}: {e}")

        return {
            "heating_setpoint": snapshot.heating_setpoint,
            "cooling_setpoint": snapshot.cooling_setpoint,
        }

    def _callback(self, state):
        try:
            if self.api.exchange.warmup_flag(state):
                return

            if not self._handles_resolved:
                self._resolve_handles(state)

            exch = self.api.exchange
            self.n_timesteps += 1

            oat = exch.get_variable_value(state, self._oat_handle)
            zt = exch.get_variable_value(state, self._zone_temp_handle)
            htg_sp = exch.get_variable_value(state, self._htg_sp_handle)
            clg_sp = exch.get_variable_value(state, self._clg_sp_handle)
            elec = exch.get_meter_value(state, self._facility_elec_handle)

            sim_hours = self.n_timesteps * 0.25

            snap = SimSnapshot(
                timestep=self.n_timesteps,
                sim_time_hours=sim_hours,
                outdoor_temp_c=oat,
                zone_temp_c=zt,
                heating_setpoint=htg_sp,
                cooling_setpoint=clg_sp,
                facility_elec_w=elec,
            )

            if self.mode == "ai-closed-loop":
                new_sp = self._query_llm(snap)
                exch.set_actuator_value(state, self._htg_actuator, new_sp["heating_setpoint"])
                exch.set_actuator_value(state, self._clg_actuator, new_sp["cooling_setpoint"])
                snap.heating_setpoint = new_sp["heating_setpoint"]
                snap.cooling_setpoint = new_sp["cooling_setpoint"]

            self.history.append(snap)
            if self.on_step:
                try:
                    self.on_step(snap)
                except Exception:
                    pass
        except Exception as e:
            self.error_log.append(f"Callback error at step {self.n_timesteps}: {e}")

    def run(self):
        self.api.runtime.callback_end_zone_timestep_after_zone_reporting(
            self.state, self._callback
        )
        os.makedirs(self.output_dir, exist_ok=True)
        argv = [
            "-w", self.epw_path,
            "-d", self.output_dir,
            "-r", self.idf_path,
        ]
        try:
            exit_code = self.api.runtime.run_energyplus(self.state, argv)
        except Exception as e:
            self.crashed = True
            self.error_log.append(f"Fatal runtime error: {e}")
            exit_code = -1

        if exit_code != 0:
            self.crashed = True

    def export_csv(self, path: str):
        if not self.history:
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        keys = list(asdict(self.history[0]).keys())
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            for s in self.history:
                writer.writerow(asdict(s))
