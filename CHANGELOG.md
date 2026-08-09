# Changelog

## 0.7.1 (experimental)

- Restored mower device grouping on Home Assistant 2026.8 by explicitly
  creating the device for its single owning config entry before entity setup.
- Kept all existing entity unique IDs and device identifiers unchanged.

## 0.7.0 (experimental)

- Added combined command/voltage activity inference for the standard mower entity.
- Added inferred `Docked` and active `Charging` binary sensors. Dock state is
  remembered after charging has been observed, including the full/maintenance phase.
- Added `Rain detected (inferred)` after an automatic run returns to charging
  without a home/stop command, low-battery indication, or another alarm.
- Corrected alarm bits 7-9 from the original EGRobot 1.0.1 application: lift
  sensor, pressure sensor, and collision sensor.
- Retained the existing unique IDs while correcting alarm names, preventing
  duplicate entities during upgrade.
- Renamed unverified alarm bits 12-14 to `Unknown alarm 12-14` instead of
  presenting speculative meanings.

## 0.6.4 (experimental)

- Added a synchronized switch for enabling or disabling the mower rain sensor.
- Reads the current state from the mower's `F` configuration response.
- Preserves every unrelated `F` setting and verifies a change by read-back.

## 0.6.3 (experimental)

- Reads model and firmware from the LAN `SearchAck` when the `V` query is unsupported.
- Stores only `ByName` and `DevName`; Wi-Fi credential fields are never retained or logged.
- Added one synchronized edge-mowing switch for every weekday.
- Edge switches preserve start time and all six area durations, then verify by read-back.
- Updated the diagnostic probe to mask `StaPd` and `ApPd` before display.

## 0.6.2 (experimental)

- Synchronizes the mower clock with Home Assistant local time at startup.
- Re-synchronizes on every new local date and whenever UTC offset or time-zone
  identity changes, covering both daylight-saving and standard-time transitions.
- Failed automatic synchronization is retried after five minutes without making
  ordinary mower status unavailable.
- Added a manual `Synchronize mower clock` button.

## 0.6.1 (experimental)

- Replaced the seven schedule sensors with directly editable text entities,
  following the approach used by Sunseeker's old wired-mower integration.
- Schedule rows use `HH:MM - HH:MM` and can be edited from the device page.
- Simple row editing preserves edge mowing and existing area allocation when
  only the start time changes.
- A changed duration is assigned safely to area 1 only when areas 2-6 are zero.
- Multi-area duration changes are rejected with guidance to use the advanced action.
- Removes obsolete schedule sensor registry entries during upgrade.

## 0.6.0 (experimental)

- Schedule sensor states now show calculated `start - end` times.
- Added the `lyfco_mower.set_schedule` action with a Home Assistant form.
- Validates six area durations against the original app's 0-250 minute range
  and 10-minute step before transmitting anything.
- Writes a schedule once, reads it back, and reports an error if verification fails.
- Made migration cleanup of the obsolete blade switch more robust.

## 0.5.1 (experimental)

- Replaced the assumed-state cutting blade switch with a stateless toggle button.
- Automatically removes the obsolete blade switch entity during upgrade.
- Publishes partial schedule/area reads immediately at startup.
- Retries incomplete extended data after 30 seconds; the five-minute cache is
  used only after all six areas and all seven schedules have been read.

## 0.5.0 (experimental)

- Added read-only discovery of all six configured working areas.
- Added read-only sensors for the seven weekday schedules.
- Schedule attributes expose edge mowing and 0–250 minutes for each of the six areas.
- Extended R/S data is cached for five minutes to avoid overloading the mower's UART bridge.
- Missing working-area and schedule replies are retried automatically.

## 0.4.2

- Added a black robot mower icon for Home Assistant and HACS.
- Documented testing on the Lyfco E1750 and possible compatibility with
  EGRobot-based Exgain, Lawnba, Ezirobot, and Devvis mowers.
- Added a standard Home Assistant lawn mower entity.
- Added automatic start, stop, and return-to-charger controls.
- Added manual mode and directional controls.
- Added an assumed-state cutting blade switch.
- Added status, firmware, voltage, runtime, charging time, and alarm entities.
- Added English default strings and Swedish translations.
- Added the standalone English Wi-Fi AP provisioning tool.
