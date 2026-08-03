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
- Assumed-state cutting blade switch.
- Machine voltage, total mowing time, total charging time, and firmware sensors.
- Fourteen decoded diagnostic alarm sensors.
- Local polling every 30 seconds with a persistent TCP connection and heartbeat.
- Standalone tool for connecting a factory/AP-mode mower to a 2.4 GHz Wi-Fi network.
- English default strings and Swedish translations.

## Important limitations

- The mower status response does not report mowing, dock, or blade state.
  Activity shown by Home Assistant is inferred from the most recent command.
- The blade uses a toggle-only command (`Y8`). Its switch is therefore marked as
  assumed state and may become out of sync after a mower restart or control from
  another app.
- The Android app contains no command for acknowledging or clearing alarms.
  Alarms clear when the physical cause is removed and the mower updates its state.
- PIN-protected control has not been implemented.

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

Status is read using `W`; firmware is read using `V`.

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
