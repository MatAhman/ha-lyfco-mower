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

