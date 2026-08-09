# Protocol notes

These notes describe behavior observed in the Lyfco Android application and on
a mower identifying itself as `M10_1.5.4`.

## LAN control

- TCP port: `9600`
- Outer framing: Miotlink VSP
- Inner commands: ASCII UART frames
- Heartbeat: `CodeName=Search`

The VSP frame begins with `0x30 0x68`. Bytes from offset 8 onward are XORed with
`0x30`. The inner mower command has this form:

```text
##LL<mark><body><CRC>\r\n
```

`CRC` is the 16-bit two's complement of the ASCII byte sum preceding the CRC.

The LAN `SearchAck` on the tested mower reports `ByName=M10` and
`DevName=M10_1.5.4`. It also exposes Wi-Fi credential fields in clear text.
Implementations must select only model/version fields and must never log or
retain the complete response.

## Verified action commands

| Action | Mark/body | Complete inner command |
| --- | --- | --- |
| Stop | `Y0` | `##08Y0FEC9` |
| Forward | `Y1` | `##08Y1FEC8` |
| Reverse | `Y2` | `##08Y2FEC7` |
| Left | `Y3` | `##08Y3FEC6` |
| Right | `Y4` | `##08Y4FEC5` |
| Automatic mowing | `Y5` | `##08Y5FEC4` |
| Manual mode | `Y6` | `##08Y6FEC3` |
| Return to charger | `Y7` | `##08Y7FEC2` |
| Toggle blade | `Y8` | `##08Y8FEC1` |

The Android app sends directional commands once on touch-down and sends no
command on touch release.

## Queries

- `V`: firmware version
- `W`: runtime, charging time, voltage, and fourteen alarm flags
- `O`: PIN information; responses were not reliable on the tested mower
- `R`: six working-area records (`R` + area + located + enabled)
- `S0`–`S6`: Sunday–Saturday schedule reads

### `W` alarm flags

The final fourteen digits of the verified `W` response are Boolean alarm
flags. The first eleven meanings below come from the original EGRobot Android
application. The application does not identify flags 12–14, so the integration
deliberately exposes those as unknown instead of assigning speculative names.

| Flag | Meaning |
| ---: | --- |
| 1 | Boundary wire broken |
| 2 | Charging station has no power |
| 3 | Battery low or abnormal |
| 4 | Left wheel motor overload |
| 5 | Right wheel motor overload |
| 6 | Blade motor overload |
| 7 | Lift sensor triggered |
| 8 | Pressure sensor triggered |
| 9 | Collision sensor triggered |
| 10 | Handle sensor triggered |
| 11 | Excessive mower tilt |
| 12–14 | Unknown alarm flags |

### Inferred activity and rain

No direct activity, dock, charging, blade-running, or rain-wet field has been
found. On the tested Lyfco E1750, moving/mowing samples were approximately
25.71–26.19 V and active charging samples were approximately 26.62–27.41 V.
The integration therefore uses 26.4 V as the active-charging threshold and
remembers the mower as docked when charge maintenance later lowers the voltage.
A verified movement command clears that dock memory.

Wet-sensor testing showed an automatically mowing machine returning to its
station without setting a distinct `W` flag. The integration reports inferred
rain only when a run started with `Y5` later reaches charging without a `Y7`
return command, a low-battery flag, or another alarm having appeared during the
run. This is a heuristic: schedule completion and commands issued from the
physical panel or another client can produce the same observations.

The verified schedule response body contains day, edge-mowing flag, `HHMM`,
and six three-digit area durations. The original app limits every area duration
to 0–250 minutes in 10-minute steps. Example for Friday, edge mowing enabled,
13:47, and 120 minutes in area 1:

```text
##31S511347120000000000000000FA6B
```

Writing uses the same `S` mark with the complete 24-character body. The
integration validates all values, transmits the write once, then issues a
read-only `S<day>` query and requires an exact match before reporting success.

## Clock setting

The original app writes the mower clock with mark `T` and a 15-digit body:

```text
YYYYMMDDWHHMMSS
```

`W` is the Java Calendar weekday (`1` Sunday through `7` Saturday). No clock
read action or `T` response parser exists in the app, so the integration can
confirm TCP transmission but cannot read the mower clock back.

No alarm acknowledgement/reset command was found in the Android app. Its alarm
dialog only displays the `W` flags and closes locally.

## AP provisioning

- Mower AP address: `192.168.4.1`
- UDP destination port: `64536`
- Client response port: randomly selected from `30000–32999`
- Discovery: `CodeName=Search&port=<client_port>`
- Configuration: `CodeName=SetWifi&...&Mode=2&port=<client_port>`

The tested mower's UDP `SearchAck` declared a VSP length of 256 bytes while
transmitting 255 bytes. The Android app ignores the declared length; the tool
tolerates this one-byte firmware defect.
