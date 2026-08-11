# Changelog

## 0.7.7 (experimental)

- Fix an explicit `Return to charger` (`Y7`) getting stuck as `Returning` with
  `Charging = off` when the mower reached the dock before battery voltage had
  fallen below the old 26.4 V residual-voltage guard.
- While an explicit return is active, recognize real charger contact from a
  sustained voltage rise: at least three significant rising samples with a
  combined rise of at least 0.10 V. Once confirmed, normal dock/charging-cycle
  inference takes over immediately.
- Keep the existing residual-voltage protection for mowing/manual departures,
  so a still-high battery voltage after leaving the dock cannot by itself create
  a false dock state.

## 0.7.6 (experimental)

- Model the measured dock maintenance cycle separately from logical dock state:
  charging rises toward about 30 V, the mower backs off above 29.5 V, reconnects
  around 28.6 V, and remains logically docked throughout that cycle.
- Use Home Assistant's local clock and the mower's stored weekly schedule to set
  `Mowing` at the configured start minute and `Returning` at the calculated end
  minute when the mower has communicated successfully within the last 75 seconds
  and no alarm is active.
- Keep real alarms and measured dock/charging behavior above schedule inference.
- Detect a mid-schedule return to the charger and keep the mower `Docked` while it
  charges, even though the configured schedule is still active.
- Detect a likely mid-schedule resume after two consecutive falling voltage samples
  at or below the measured 28.6 V reconnect level, then return Home Assistant to
  `Mowing` until another real dock event is observed.
- Keep explicit Home Assistant pause/return commands above schedule inference.
- Restore the proven `CodeName=Search` TCP heartbeat every five seconds after the
  experimental 0x1A heartbeat proved less stable on the tested mower.

## 0.7.5 (experimental)

- Detect internal schedule starts from the configured weekday/start time and
  the charging-voltage drop, so scheduled mowing changes the mower entity from
  `Docked` to `Mowing` even though Home Assistant did not send a start command.
- Added `@MatAhman` as the integration code owner in the manifest for Home
  Assistant and HACS validation.

## 0.7.4 (experimental)

- Prevented residual high battery voltage after leaving the charger from
  incorrectly changing `Mowing` or `Returning` back to `Docked`.
- Require a below-threshold voltage sample after a movement command before a
  later high voltage can establish a new dock event.
- Keep the last confirmed status through two transient polling failures and
  report unavailable only on the third consecutive failure.
- Reset a connection immediately after an optional extended-read failure and
  reduce automatic area/schedule refreshes from five to fifteen minutes.
- Restored repository validation files and corrected stale schedule wording in
  the README.

## 0.7.3 (experimental)

- Republished the complete status update with a new version so HACS cannot
  reuse an earlier cached 0.7.2 archive.
- Includes the `Docked`, `Charging`, and inferred rain binary sensors.
- Includes TCP EOF recovery and the schedule action YAML without merge keys.

## 0.7.2 (experimental)

- Treat an unexpected TCP EOF from the Miotlink bridge as a recoverable
  connection failure.
- Reset the stale stream and retry the status query on a fresh socket instead
  of leaving the coordinator with an unexpected `IncompleteReadError`.
- Removed YAML merge keys from the schedule action description to prevent
  duplicate `name` warnings in Home Assistant 2026.8.

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
