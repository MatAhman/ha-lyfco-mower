# Lyfco Robot Mower for Home Assistant

<p align="center">
  <img src="custom_components/lyfco_mower/brand/icon.png" width="128" alt="Black robot lawn mower icon">
</p>

Local Home Assistant integration and Wi-Fi provisioning tool for selected
Lyfco/Miotlink robot mowers. Communication stays on the local network; no cloud
account or original Android app is required after provisioning.

> [!WARNING]
> This is an independent reverse-engineering project and is not affiliated with
> Lyfco, Miotlink, or Home Assistant. Mowers and cutting blades can cause injury.
> Test controls outdoors, keep the mower in sight, and keep the physical stop
> control within reach.

## Compatibility

The integration has been tested on a **Lyfco E1750** with:

- mower protocol/model identifier `M10`;
- firmware/device identifier `M10_1.5.4`;
- Android app version 6.2.1;
- local TCP control on port 9600;
- Miotlink AP provisioning at `192.168.4.1`.

It may also work with other robot mowers that use the **EGRobot** app,
including models sold under the Exgain, Lawnba, Ezirobot, and Devvis brands.
These additional brands have not yet been tested with this integration, so
compatibility is not guaranteed. Reports and protocol observations are welcome.

The included integration icon is shown locally by Home Assistant 2026.3 and
newer. The icon is also included as a HACS brand asset.

## Features

- Standard Home Assistant `lawn_mower` entity.
- Start automatic mowing, stop/pause, and return to charger.
- Manual mode with forward, reverse, left, and right controls.
- Stateless cutting blade toggle button.
- Machine voltage, total mowing time, total charging time, and firmware sensors.
- Fourteen decoded diagnostic alarm sensors.
- Read-only working-area configuration and seven weekday schedule sensors.
- Seven directly editable weekday rows displayed as `start - end` ranges.
- Schedule editing through the `lyfco_mower.set_schedule` action with read-back verification.
- Automatic mower-clock synchronization, including daylight-saving changes.
- Model and firmware discovery from the LAN handshake.
- One synchronized edge-mowing switch for each weekday.
- Synchronized switch for enabling or disabling the mower rain sensor.
- Local polling every 30 seconds with a persistent TCP connection and heartbeat.
- Standalone tool for connecting a factory/AP-mode mower to a 2.4 GHz Wi-Fi network.
- English default strings and Swedish translations.

## Important limitations

- The mower status response does not report mowing, dock, or blade state.
  Activity shown by Home Assistant is inferred from the most recent command.
- The blade uses a toggle-only command (`Y8`) and the mower reports no blade
  state. It is therefore exposed as a stateless button rather than a switch.
- The Android app contains no command for acknowledging or clearing alarms.
  Alarms clear when the physical cause is removed and the mower updates its state.
- PIN-protected control has not been implemented.
- The mower firmware/app protocol provides no clock-read command, so clock sync
  confirms successful transmission but cannot verify the displayed mower time.
- Miotlink `SearchAck` includes Wi-Fi credential fields in clear text. The
  integration extracts only model/firmware and never stores or logs the full response.
- Working-area entities remain read-only. Schedules can be changed through a
  validated action and are read back after every write. Extended data is
  refreshed at most once every five minutes after a complete read.

## Installation

### Manual installation

1. Copy `custom_components/lyfco_mower` into the Home Assistant configuration
   directory so the final path is:

   ```text
   /config/custom_components/lyfco_mower/manifest.json
   ```

2. Restart Home Assistant.
3. Open **Settings → Devices & services → Add integration**.
4. Search for **Lyfco Robot Mower**.
5. Enter the mower's reserved local IP address or hostname.

The mower should have a DHCP reservation because the integration currently uses
the configured address as its unique identifier.

### HACS custom repository

After this project has been uploaded to GitHub:

1. Open HACS.
2. Open the menu and select **Custom repositories**.
3. Enter the GitHub repository URL.
4. Select category **Integration**.
5. Install **Lyfco Robot Mower** and restart Home Assistant.

HACS can install from the default branch. GitHub Releases are optional but make
version selection and upgrades clearer.

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
is Sunday. Each schedule sensor uses the start time as its state and exposes
edge mowing plus minutes for areas 1–6 as attributes.

### Changing a schedule

Open the mower's device page and select a weekday row. Enter a schedule in the
format `HH:MM - HH:MM`, for example `13:47 - 15:47`. The duration must be in
10-minute steps and can be at most 250 minutes when only area 1 is used. Edge
mowing is preserved.

If a day uses several working areas, changing only the start time preserves all
area durations. Changing the total duration is blocked because a single time
range cannot describe how the new duration should be divided. For that advanced
case, use **Developer tools → Actions → Lyfco Robot Mower: Set weekday schedule**.

Each weekday also has an **Edge mowing** switch on the device page. These are
real synchronized switches: the current value comes from the mower schedule,
and changing one preserves that day's start time and all six area durations.

The **Rain sensor** switch reads its actual enabled/disabled setting from the
mower. A change preserves all other `F` configuration fields and is accepted
only after the integration has read the complete setting back from the mower.
This switch controls whether the sensor is used; it does not indicate whether
the sensor is currently wet.

### Clock synchronization

The mower clock is set from Home Assistant's configured local time when the
integration starts, at every new local date, and whenever the local UTC offset
or time-zone identity changes. This explicitly covers transitions into and out
of daylight-saving time. A failed automatic attempt is retried after five
minutes. The device page also provides a manual **Synchronize mower clock**
button.

## Troubleshooting

- Confirm Home Assistant can reach the mower on TCP port 9600.
- Confirm no other application is holding the mower's single active TCP session.
- Restart Home Assistant after replacing integration files.
- Hard-refresh the browser if translated names are cached.
- Check **Settings → System → Logs** for `lyfco_mower`.
- For AP discovery, verify the computer has an address such as `192.168.4.2` and
  can ping `192.168.4.1`.

## Publishing this repository

1. Create a public GitHub repository, for example `ha-lyfco-mower`.
2. Upload the **contents** of this directory to the repository root.
3. Commit and push the files.
4. Optionally create a release tagged `v0.6.4`.

Example command-line workflow:

```bash
git init
git add .
git commit -m "Add synchronized rain sensor switch in v0.6.4"
git branch -M main
git remote add origin https://github.com/MatAhman/ha-lyfco-mower.git
git push -u origin main
```

## License

MIT — see [LICENSE](LICENSE).
