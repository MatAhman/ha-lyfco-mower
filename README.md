# EGROBOT Mower for Home Assistant

<p align="center">
  <img src="custom_components/lyfco_mower/brand/icon.png" width="128" alt="Black robot lawn mower icon">
</p>

Local Home Assistant integration and Wi-Fi provisioning tool for robot mowers
using the **EGROBOT / Miotlink** local protocol. Communication stays on the
local network; no cloud account or original Android app is required after
provisioning.

The integration keeps the historical Home Assistant domain `lyfco_mower` and
repository name `ha-lyfco-mower` for backwards compatibility.

> [!WARNING]
> This is an independent reverse-engineering project and is not affiliated with
> any mower brand, EGROBOT, Miotlink, Zhejiang Tianchen, or Home Assistant.
> Mowers and cutting blades can cause injury. Test controls outdoors, keep the
> mower in sight, and keep the physical stop control within reach.

## Compatibility

The integration has been physically tested on a **Lyfco E1750** with:

- mower protocol/model identifier `M10`;
- firmware/device identifier `M10_1.5.4`;
- Android app version 6.2.1;
- local TCP control on port 9600;
- Miotlink AP provisioning at `192.168.4.1`.

The following brands/models are known to use the EGROBOT app or the same
EGROBOT/Tianchen family and are therefore **expected to be compatible**, but
have not been physically tested with this integration unless stated otherwise:

- Lyfco
- Exgain
- Lawnba
- Ezirobot
- Devvis
- RobotZoo Wombat
- Maxton
- GEM
- Seon
- VERTAK
- FUXTEC
- NAC
- Land Shark
- ROOKS
- AutoLawnMow / Genie
- E.ZICOM / e.zigreen

Using the same EGROBOT app is a strong compatibility indicator, but different
firmware generations may expose slightly different status fields or optional
features. Reports and diagnostics from additional models are welcome.

## Features

- Standard Home Assistant `lawn_mower` entity.
- Start automatic mowing, stop/pause, and return to charger.
- Manual mode with forward, reverse, left, and right controls.
- Stateless cutting blade toggle button.
- Machine voltage, total mowing time, total charging time, firmware, estimated
  battery percentage, and minute-resolution current charging-time sensors.
- Connectivity binary sensor showing whether the mower is responding, including
  last-seen and consecutive-failure diagnostics.
- Fourteen known alarm positions on newer firmware, while older 11-alarm
  EGROBOT status responses are accepted and padded safely.
- Inferred mowing, returning, docked, and active-charging status from one final
  state machine shared by the mower, Docked, and Charging entities.
- Passive charger detection from sustained voltage behavior, including recovery
  after an integration reload while a schedule is still active.
- Inferred rain detection when an automatic run returns to charge without a
  home command, low-battery indication, or another alarm.
- Read-only working-area configuration and seven editable weekday schedule rows.
- Seven directly editable weekday rows displayed as `start - end` ranges.
- Schedule editing through the `lyfco_mower.set_schedule` action with up to
  three read-back verification attempts.
- Automatic mower-clock synchronization, including daylight-saving changes.
- Model and firmware discovery from the LAN handshake.
- One synchronized edge-mowing switch for each weekday.
- Synchronized switch for enabling or disabling the mower rain sensor.
- Normal polling every 30 seconds with 10-second fast polling for three minutes
  around verified commands, state transitions, and schedule boundaries.
- Diagnostic transition history, voltage history, and latest/previous mowing
  sessions to support protocol testing.
- Standalone tool for connecting a factory/AP-mode mower to a 2.4 GHz Wi-Fi network.
- English default strings and Swedish translations.

## Alarm compatibility

Older EGROBOT applications V1.0.1/V4.2.2 use 11 alarm positions. Later firmware,
including the physically tested Lyfco E1750 generation, uses 14. The integration
accepts both layouts.

The final three newer positions are mapped as:

12. Wire signal lost
13. Outside boundary
14. Mower stuck

Existing entity unique IDs are preserved during the rename, so upgrading does
not create duplicate alarm entities.

## Important limitations

- The mower status response does not directly report mowing, dock, rain, blade,
  or battery state of charge. Absolute voltage values are supporting context
  only; a single voltage cannot establish dock state.
- Charging and battery-voltage behavior has been calibrated on the tested Lyfco
  E1750/M10. Other EGROBOT models may use different battery packs or charging
  voltages, so estimated battery percentage and inferred charging state require
  additional physical validation on those models.
- The estimated battery percentage currently maps 24.0 V to 5% and 29.0 V or
  higher to 100%, with linear interpolation in between. It is not BMS SoC.
- The mower's cumulative charging counter is reported only in whole hours. The
  current charging-time sensor measures the currently inferred charging phase in
  minutes from Home Assistant observations.
- `Rain detected (inferred)` is not a physical wet-contact reading. It indicates
  a likely reason for an otherwise unexplained automatic return and can be wrong.
- A mower can return to charge by itself during an active schedule. The current
  state machine can recognize the dock/charge state passively. Home Assistant
  does not force an automatic resume.
- The tested mower was observed to resume scheduled work autonomously after a
  mid-schedule charge, even after the nominal wall-clock schedule end. Correct
  post-charge continuation inference is intentionally left for a later beta
  because the mower is currently unavailable for physical testing.
- The blade uses a toggle-only command (`Y8`) and the mower reports no blade
  state. It is therefore exposed as a stateless button rather than a switch.
- The Android app contains no command for acknowledging or clearing alarms.
- PIN-protected control has not been implemented.
- The mower firmware/app protocol provides no clock-read command, so clock sync
  confirms transmission but cannot verify the displayed mower time.
- Miotlink `SearchAck` includes Wi-Fi credential fields in clear text. The
  integration extracts only model/firmware and never stores or logs the full response.
- Working-area entities remain read-only. Schedules can be changed through a
  validated action and are read back after every write.

## Installation

### Manual installation

1. Copy `custom_components/lyfco_mower` into the Home Assistant configuration
   directory so the final path is:

   ```text
   /config/custom_components/lyfco_mower/manifest.json
   ```

2. Restart Home Assistant.
3. Open **Settings → Devices & services → Add integration**.
4. Search for **EGROBOT Mower**.
5. Enter the mower's reserved local IP address or hostname.

The mower should have a DHCP reservation because the integration currently uses
the configured address as its unique identifier.

### HACS custom repository

1. Open HACS.
2. Open the menu and select **Custom repositories**.
3. Enter this GitHub repository URL.
4. Select category **Integration**.
5. Install **EGROBOT Mower** and restart Home Assistant.

The repository and integration domain remain named `ha-lyfco-mower` and
`lyfco_mower` for backwards compatibility.

## Wi-Fi provisioning without the original app

The provisioning tool only uses Python's standard library.

1. Put the mower into AP/pairing mode.
2. Connect the computer manually to a network named `MLinkAp_*` or `MiotLinkAp_*`.
3. Ignore the operating system's “no internet” warning.
4. Run read-only discovery:

   ```bash
   python3 tools/lyfco_wifi_setup.py discover
   ```

5. If discovery succeeds, provision the mower:

   ```bash
   python3 tools/lyfco_wifi_setup.py configure --ssid "YOUR_2_4_GHZ_SSID"
   ```

The password is requested using hidden input. The tool displays a summary and
requires typing `CONFIGURE` before it sends `SetWifi`. The mower AP may disappear
before `SetWifiAck` is received; wait 30–60 seconds and check the router client list.

Reserve the mower's new address in the router before adding it to Home Assistant.

## Entities and commands

| Home Assistant control | Protocol command |
| --- | --- |
| Start automatic mowing | `Y5` |
| Stop/pause | `Y0` |
| Return to charger | `Y7` |
| Enable manual mode | `Y6` |
| Forward / reverse | `Y1` / `Y2` |
| Left / right | `Y3` / `Y4` |
| Toggle cutting blade | `Y8` |

Status is read using `W`; firmware is read using `V`. Working areas are read
using `R`, and weekday schedules are read using `S0` through `S6` where day 0
is Sunday. The known schedule format contains one start time per day plus six
area-duration fields.

### Changing a schedule

Open the mower's device page and select a weekday row. Enter a schedule in the
format `HH:MM - HH:MM`. The duration must be in 10-minute steps and can be at
most 250 minutes when only area 1 is used. Edge mowing is preserved.

If a day uses several working areas, changing only the start time preserves all
area durations. For advanced allocation, use **Developer tools → Actions →
EGROBOT Mower: Set weekday schedule**.

A schedule is written once and then read back up to three times. A temporary
stale read-back therefore does not fail the edit immediately, while a persistent
mismatch is reported as an error and included in diagnostics.

Each weekday also has an **Edge mowing** switch. The **Rain sensor** switch reads
and writes the mower's actual enabled/disabled configuration while preserving
all unrelated `F` settings.

### Clock synchronization

The mower clock is set from Home Assistant's configured local time when the
integration starts, on every new local date, and whenever the local UTC offset
or time-zone identity changes. A failed automatic attempt is retried after five
minutes. The device page also provides a manual **Synchronize mower clock** button.

## Troubleshooting

- Confirm Home Assistant can reach the mower on TCP port 9600.
- Confirm no other application is holding the mower's single active TCP session.
- Restart Home Assistant after replacing integration files.
- Hard-refresh the browser if translated names are cached.
- Check **Settings → System → Logs** for `lyfco_mower`.
- For AP discovery, verify the computer has an address such as `192.168.4.2` and
  can ping `192.168.4.1`.

## License

MIT — see [LICENSE](LICENSE).
