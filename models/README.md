# Building models

Put your baseline `.idf` here as `baseline.idf`.

Fastest source: EnergyPlus ships example files at
`$ENERGYPLUS_DIR/ExampleFiles/`. Good candidates for this hackathon
(single/multi-zone, has thermostats + HVAC you can actuate via EMS):

- `RefBldgMediumOfficeNew2004_Chicago.idf` — 3-floor medium office, VAV system
- `5ZoneAirCooled.idf` — simpler 5-zone model, good for a fast demo loop
- `RefBldgSmallOfficeNew2004_Chicago.idf` — smallest/fastest to iterate on

You also need a weather file (`.epw`) from `$ENERGYPLUS_DIR/WeatherData/`,
e.g. `USA_IL_Chicago-OHare.Intl.AP.725300_TMY3.epw`.

## Enabling EMS actuators

Your idf needs `EnergyManagementSystem:Actuator` objects exposed for
whatever you want the LLM to control — typically zone thermostat
heating/cooling setpoints, or supply air temperature setpoints. If the
example file doesn't already expose these, add e.g.:

```
EnergyManagementSystem:Actuator,
  ZoneCoolingSetpoint,           !- Name
  Core_ZN ZN,                    !- Actuated Component Unique Name (zone name)
  Zone Temperature Control,      !- Actuated Component Type
  Cooling Setpoint;              !- Actuated Component Control Type

EnergyManagementSystem:Actuator,
  ZoneHeatingSetpoint,
  Core_ZN ZN,
  Zone Temperature Control,
  Heating Setpoint;
```

Repeat per zone you want to control. `energyplus_wrapper.py` looks up
actuator handles by these names at runtime, so keep names consistent with
`ZONE_LIST` in that file (or edit it to match your idf's zone names).
