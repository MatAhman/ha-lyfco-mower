"""Local TCP protocol for Lyfco/Miotlink robot mowers."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import datetime
import logging
import time

from .const import ALARM_KEYS, CHARGING_VOLTAGE, DEFAULT_PORT

_LOGGER = logging.getLogger(__name__)

VSP_HEADER_SIZE = 20
CONNECT_TIMEOUT = 10.0
RESPONSE_TIMEOUT = 6.0
HEARTBEAT_INTERVAL = 5.0
EXTENDED_REFRESH_INTERVAL = 300.0
INCOMPLETE_REFRESH_INTERVAL = 30.0
EXTENDED_RESPONSE_TIMEOUT = 2.5


class LyfcoError(Exception):
    """Base error for Lyfco communication."""


class LyfcoConnectionError(LyfcoError):
    """The mower could not be reached."""


class LyfcoProtocolError(LyfcoError):
    """The mower returned an invalid or unsupported response."""


@dataclass(frozen=True, slots=True)
class MowerArea:
    """One configured working area."""

    number: int
    located: bool
    enabled: bool


@dataclass(frozen=True, slots=True)
class MowerSchedule:
    """Read-only schedule for one weekday (0=Sunday)."""

    day: int
    edge_mowing: bool
    start_time: str
    area_minutes: tuple[int, int, int, int, int, int]


@dataclass(frozen=True, slots=True)
class MowerConfiguration:
    """Configuration returned by the mower's F command."""

    mower_address: int
    language_code: int
    range_level: int
    ultrasonic_sensor: bool
    rain_sensor: bool
    touch_sensor: bool
    pressure_sensor: bool
    compass: bool
    audible_alarm: bool

    def as_body(self) -> str:
        """Encode the complete F body, preserving every configuration field."""
        return (
            f"{self.mower_address:02d}{self.language_code:02d}{self.range_level}"
            f"{int(self.ultrasonic_sensor)}{int(self.rain_sensor)}"
            f"{int(self.touch_sensor)}{int(self.pressure_sensor)}"
            f"{int(self.compass)}{int(self.audible_alarm)}"
        )


@dataclass(frozen=True, slots=True)
class MowerStatus:
    """Decoded status returned by the mower."""

    runtime_hours: int
    charge_hours: int
    voltage: float
    alarm_flags: tuple[bool, ...]
    firmware: str | None = None
    model: str | None = None
    configuration: MowerConfiguration | None = None
    areas: tuple[MowerArea, ...] = ()
    schedules: tuple[MowerSchedule, ...] = ()
    inferred_activity: str | None = None
    docked: bool = False
    charging: bool = False
    rain_detected_inferred: bool = False
    inference_source: str | None = None

    @property
    def active_alarm_keys(self) -> tuple[str, ...]:
        """Return keys for all active alarms."""
        return tuple(
            ALARM_KEYS[index]
            for index, active in enumerate(self.alarm_flags)
            if active and index < len(ALARM_KEYS)
        )


def build_vsp(payload: str) -> bytes:
    """Wrap a Miotlink payload in the app's 20-byte VSP frame."""
    raw = payload.encode("latin-1")
    total = len(raw) + VSP_HEADER_SIZE
    frame = bytearray(total)
    frame[0:2] = b"0h"
    frame[2:4] = total.to_bytes(2, "big")
    frame[8] = 0x65
    frame[10:12] = (total - 8).to_bytes(2, "big")
    frame[15] = 1
    frame[VSP_HEADER_SIZE:] = raw
    for index in range(8, total):
        frame[index] ^= 0x30
    return bytes(frame)


def decode_vsp(frame: bytes) -> str:
    """Decode one complete VSP frame."""
    if len(frame) < VSP_HEADER_SIZE or frame[0:2] != b"0h":
        raise LyfcoProtocolError("Invalid VSP header")
    if int.from_bytes(frame[2:4], "big") != len(frame):
        raise LyfcoProtocolError("Invalid VSP length")
    decoded = bytearray(frame)
    for index in range(8, len(decoded)):
        decoded[index] ^= decoded[0]
    return bytes(decoded[VSP_HEADER_SIZE:]).decode("latin-1", errors="replace")


def _crc(prefix: str) -> str:
    return f"{(-sum(ord(character) for character in prefix)) & 0xFFFF:04X}"


def build_command(mark: str, body: str = "") -> str:
    """Build the inner ASCII mower command."""
    declared_length = len(mark) + len(body) + 6
    if declared_length > 99:
        raise LyfcoProtocolError("Command is too long")
    prefix = f"##{declared_length:02d}{mark}{body}"
    return f"{prefix}{_crc(prefix)}\r\n"


def verify_command(command: str) -> bool:
    """Validate mower command structure and checksum."""
    if len(command) < 10 or not command.startswith("##"):
        return False
    try:
        declared_length = int(command[2:4])
    except ValueError:
        return False
    if len(command) < declared_length + 4:
        return False
    crc_start = declared_length - 2
    return command[crc_start : crc_start + 4].upper() == _crc(command[:crc_start])


def _uart_envelope(command: str) -> str:
    return (
        f"CodeName=GetUartData&Chn=0&Len={len(command)}"
        f"&UserBinaryData={command}"
    )


def _extract_uart(message: str) -> str | None:
    marker = "&UserBinaryData="
    if marker not in message:
        return None
    header, data = message.split(marker, 1)
    length = len(data)
    for field in header.split("&"):
        if field.startswith("Len="):
            with suppress(ValueError):
                length = int(field[4:])
    return data[:length]


def parse_status(
    command: str,
    firmware: str | None = None,
    model: str | None = None,
) -> MowerStatus:
    """Parse the W response used by app version 6.2.1."""
    if not verify_command(command) or command[4:5] != "W":
        raise LyfcoProtocolError("Invalid status response")
    declared_length = int(command[2:4])
    body = command[5 : declared_length - 2]
    if len(body) < 25 or not body.isdigit():
        raise LyfcoProtocolError("Unsupported W response body")
    flags = tuple(value != "0" for value in body[14:])
    flags = (flags + (False,) * len(ALARM_KEYS))[: len(ALARM_KEYS)]
    return MowerStatus(
        runtime_hours=int(body[0:6]),
        charge_hours=int(body[6:10]),
        voltage=int(body[10:14]) / 100.0,
        alarm_flags=flags,
        firmware=firmware,
        model=model,
    )


def parse_search_ack(message: str) -> tuple[str | None, str | None]:
    """Extract only model and firmware; never retain credential fields."""
    if not message.startswith("CodeName=SearchAck"):
        return None, None
    fields: dict[str, str] = {}
    for item in message.split("&"):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        if key in {"DevName", "ByName"}:
            fields[key] = value
    device_name = fields.get("DevName", "")
    model = fields.get("ByName") or device_name.split("_", 1)[0] or None
    firmware = device_name.split("_", 1)[1] if "_" in device_name else None
    return model, firmware


def parse_area(command: str) -> MowerArea:
    """Parse one verified R working-area record."""
    if not verify_command(command) or command[4:5] != "R":
        raise LyfcoProtocolError("Invalid working-area response")
    declared_length = int(command[2:4])
    body = command[5 : declared_length - 2]
    if len(body) != 3 or not body.isdigit() or body[0] not in "123456":
        raise LyfcoProtocolError("Unsupported working-area response")
    return MowerArea(
        number=int(body[0]),
        located=body[1] == "1",
        enabled=body[2] == "1",
    )


def parse_configuration(command: str) -> MowerConfiguration:
    """Parse one verified F configuration response."""
    if not verify_command(command) or command[4:5] != "F":
        raise LyfcoProtocolError("Invalid configuration response")
    declared_length = int(command[2:4])
    body = command[5 : declared_length - 2]
    if (
        len(body) != 11
        or not body.isdigit()
        or any(value not in "01" for value in body[5:])
    ):
        raise LyfcoProtocolError("Unsupported configuration response")
    range_level = int(body[4])
    if range_level > 4:
        raise LyfcoProtocolError(
            "Configuration response contains an invalid range level"
        )
    return MowerConfiguration(
        mower_address=int(body[0:2]),
        language_code=int(body[2:4]),
        range_level=range_level,
        ultrasonic_sensor=body[5] == "1",
        rain_sensor=body[6] == "1",
        touch_sensor=body[7] == "1",
        pressure_sensor=body[8] == "1",
        compass=body[9] == "1",
        audible_alarm=body[10] == "1",
    )


def parse_schedule(command: str) -> MowerSchedule:
    """Parse one verified S weekday schedule."""
    if not verify_command(command) or command[4:5] != "S":
        raise LyfcoProtocolError("Invalid schedule response")
    declared_length = int(command[2:4])
    body = command[5 : declared_length - 2]
    if len(body) != 24 or not body.isdigit() or body[0] not in "0123456":
        raise LyfcoProtocolError("Unsupported schedule response")
    hour = int(body[2:4])
    minute = int(body[4:6])
    area_minutes = tuple(int(body[index : index + 3]) for index in range(6, 24, 3))
    if hour > 23 or minute > 59 or any(value > 250 for value in area_minutes):
        raise LyfcoProtocolError("Schedule response contains out-of-range values")
    return MowerSchedule(
        day=int(body[0]),
        edge_mowing=body[1] == "1",
        start_time=f"{hour:02d}:{minute:02d}",
        area_minutes=area_minutes,  # type: ignore[arg-type]
    )


class LyfcoMowerClient:
    """Maintain a local mower connection and request status."""

    def __init__(self, host: str, port: int = DEFAULT_PORT) -> None:
        self.host = host
        self.port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._firmware: str | None = None
        self._model: str | None = None
        self._configuration: MowerConfiguration | None = None
        self._pin_checked = False
        self._pin_enabled = False
        self._areas: tuple[MowerArea, ...] = ()
        self._schedules: tuple[MowerSchedule, ...] = ()
        self._extended_refreshed_at = 0.0
        self._last_action: str | None = None
        self._activity: str | None = None
        self._docked_latched = False
        self._rain_detected_inferred = False
        self._alarm_seen_since_auto = False
        self._low_battery_seen_since_auto = False

    async def async_get_status(self) -> MowerStatus:
        """Request status, reconnecting once if necessary."""
        async with self._lock:
            last_error: Exception | None = None
            for attempt in range(2):
                try:
                    new_connection = await self._async_connect_locked()
                    if new_connection:
                        await self._async_send_locked("CodeName=Search")
                        await self._async_send_command_locked("V")
                    await self._async_send_command_locked("W")
                    status = await self._async_wait_for_status_locked()
                    extended_complete = (
                        self._configuration is not None
                        and len(self._areas) == 6
                        and len(self._schedules) == 7
                    )
                    refresh_interval = (
                        EXTENDED_REFRESH_INTERVAL
                        if extended_complete
                        else INCOMPLETE_REFRESH_INTERVAL
                    )
                    if (
                        time.monotonic() - self._extended_refreshed_at
                        >= refresh_interval
                    ):
                        try:
                            await self._async_refresh_extended_locked()
                        except (OSError, asyncio.TimeoutError, LyfcoError) as error:
                            # Extended data is optional; never make core status
                            # unavailable merely because an R/S reply was lost.
                            _LOGGER.debug("Lyfco extended read failed: %s", error)
                    status = self._infer_status(status)
                    return replace(
                        status,
                        configuration=self._configuration,
                        areas=self._areas,
                        schedules=self._schedules,
                    )
                except (OSError, asyncio.TimeoutError, LyfcoError) as error:
                    last_error = error
                    _LOGGER.debug(
                        "Lyfco query attempt %s failed: %s", attempt + 1, error
                    )
                    await self._async_reset_locked()
            raise LyfcoConnectionError(
                f"No valid response from {self.host}:{self.port}: {last_error}"
            ) from last_error

    async def async_stop(self) -> None:
        """Send the app's Y0 stop command."""
        await self._async_send_action("0", "stop")

    async def async_start_auto(self) -> None:
        """Send the app's Y5 automatic mowing command."""
        await self._async_send_action("5", "automatic start")

    async def async_go_home(self) -> None:
        """Send the app's Y7 return-to-charger command."""
        await self._async_send_action("7", "return to charger")

    async def async_manual_mode(self) -> None:
        """Send the app's Y6 manual-mode command."""
        await self._async_send_action("6", "manual mode")

    async def async_manual_forward(self) -> None:
        """Send the app's Y1 forward command."""
        await self._async_send_action("1", "manual forward")

    async def async_manual_reverse(self) -> None:
        """Send the app's Y2 reverse command."""
        await self._async_send_action("2", "manual reverse")

    async def async_manual_left(self) -> None:
        """Send the app's Y3 left command."""
        await self._async_send_action("3", "manual left")

    async def async_manual_right(self) -> None:
        """Send the app's Y4 right command."""
        await self._async_send_action("4", "manual right")

    async def async_toggle_blade(self) -> None:
        """Send the app's Y8 blade toggle command."""
        await self._async_send_action("8", "toggle cutting blade")

    async def async_sync_clock(self, local_time: datetime) -> None:
        """Set the mower clock using the exact T format used by the app."""
        if local_time.tzinfo is None or local_time.utcoffset() is None:
            raise LyfcoProtocolError("Clock synchronization requires local time")
        # Java Calendar uses Sunday=1 through Saturday=7.
        weekday = (local_time.weekday() + 1) % 7 + 1
        body = (
            f"{local_time:%Y%m%d}{weekday}"
            f"{local_time:%H%M%S}"
        )
        async with self._lock:
            new_connection = await self._async_connect_locked()
            if new_connection:
                await self._async_send_locked("CodeName=Search")
            await self._async_send_command_locked("T", body)

    async def async_set_schedule(
        self,
        day: int,
        start_hour: int,
        start_minute: int,
        edge_mowing: bool,
        area_minutes: tuple[int, int, int, int, int, int],
    ) -> MowerSchedule:
        """Write one weekday schedule and verify it by reading it back."""
        if (
            day not in range(7)
            or start_hour not in range(24)
            or start_minute not in range(60)
        ):
            raise LyfcoProtocolError("Invalid schedule day or start time")
        if any(value < 0 or value > 250 or value % 10 for value in area_minutes):
            raise LyfcoProtocolError(
                "Area minutes must be 0-250 in steps of 10 minutes"
            )
        body = (
            f"{day}{int(edge_mowing)}{start_hour:02d}{start_minute:02d}"
            + "".join(f"{value:03d}" for value in area_minutes)
        )
        expected = MowerSchedule(
            day=day,
            edge_mowing=edge_mowing,
            start_time=f"{start_hour:02d}:{start_minute:02d}",
            area_minutes=area_minutes,
        )

        async with self._lock:
            new_connection = await self._async_connect_locked()
            if new_connection:
                await self._async_send_locked("CodeName=Search")
            # Send the write exactly once. Any retry below is read-only.
            await self._async_send_command_locked("S", body)
            await asyncio.sleep(0.3)
            confirmed: MowerSchedule | None = None
            for _attempt in range(2):
                commands = await self._async_collect_read_group_locked(
                    (("S", str(day)),)
                )
                for command in commands:
                    with suppress(LyfcoProtocolError):
                        schedule = parse_schedule(command)
                        if schedule.day == day:
                            confirmed = schedule
                if confirmed == expected:
                    schedules = {
                        schedule.day: schedule for schedule in self._schedules
                    }
                    schedules[day] = confirmed
                    self._schedules = tuple(
                        schedules[index] for index in sorted(schedules)
                    )
                    return confirmed
            if confirmed is None:
                raise LyfcoConnectionError(
                    "The mower did not return the written schedule"
                )
            raise LyfcoProtocolError(
                "The schedule read back from the mower did not match the "
                "requested values"
            )

    async def async_set_rain_sensor(self, enabled: bool) -> MowerConfiguration:
        """Change only the rain-sensor flag and verify the F configuration."""
        async with self._lock:
            if self._configuration is None:
                raise LyfcoProtocolError(
                    "The mower configuration has not been read yet; try again "
                    "after the next update"
                )
            expected = replace(self._configuration, rain_sensor=enabled)
            if expected == self._configuration:
                return expected

            new_connection = await self._async_connect_locked()
            if new_connection:
                await self._async_send_locked("CodeName=Search")
            # F writes the complete configuration. Build it from the last valid
            # mower response so unrelated sensor and language settings survive.
            await self._async_send_command_locked("F", expected.as_body())
            await asyncio.sleep(0.3)
            confirmed: MowerConfiguration | None = None
            for _attempt in range(2):
                commands = await self._async_collect_read_group_locked((("F", ""),))
                for command in commands:
                    with suppress(LyfcoProtocolError):
                        confirmed = parse_configuration(command)
                if confirmed == expected:
                    self._configuration = confirmed
                    return confirmed
            if confirmed is None:
                raise LyfcoConnectionError(
                    "The mower did not return its configuration after the change"
                )
            raise LyfcoProtocolError(
                "The configuration read back from the mower did not match the "
                "requested rain-sensor setting"
            )

    async def _async_send_action(self, action: str, description: str) -> None:
        """Send one Y action directly without a blocking PIN query."""
        async with self._lock:
            last_error: Exception | None = None
            for attempt in range(2):
                try:
                    new_connection = await self._async_connect_locked()
                    if new_connection:
                        await self._async_send_locked("CodeName=Search")
                    await self._async_send_command_locked("Y", action)
                    self._record_action(action)
                    return
                except (OSError, asyncio.TimeoutError, LyfcoConnectionError) as error:
                    last_error = error
                    _LOGGER.debug(
                        "Lyfco %s attempt %s failed: %s",
                        description,
                        attempt + 1,
                        error,
                    )
                    await self._async_reset_locked()
                except LyfcoProtocolError:
                    raise
            raise LyfcoConnectionError(
                f"Unable to send {description} to {self.host}:{self.port}: "
                f"{last_error!r}"
            ) from last_error

    def _record_action(self, action: str) -> None:
        """Remember a verified action for the next inferred status update."""
        if action == "0":
            self._activity = "paused"
        elif action == "5":
            self._activity = "mowing"
            self._docked_latched = False
            self._rain_detected_inferred = False
            self._alarm_seen_since_auto = False
            self._low_battery_seen_since_auto = False
        elif action == "7":
            self._activity = "returning"
            self._docked_latched = False
            self._rain_detected_inferred = False
        elif action == "6":
            self._activity = "paused"
            self._docked_latched = False
            self._rain_detected_inferred = False
        elif action in {"1", "2", "3", "4"}:
            self._activity = "mowing"
            self._docked_latched = False
            self._rain_detected_inferred = False
        # Y8 is a stateless blade toggle and does not establish mower motion.
        self._last_action = action

    def _infer_status(self, status: MowerStatus) -> MowerStatus:
        """Combine voltage and the last command into a conservative state."""
        charging = status.voltage >= CHARGING_VOLTAGE
        source = "last_command" if self._activity is not None else None
        if self._last_action == "5":
            self._alarm_seen_since_auto |= any(status.alarm_flags)
            if len(status.alarm_flags) >= 3:
                self._low_battery_seen_since_auto |= status.alarm_flags[2]

        if charging:
            # Two recorded tests showed 26.62-27.41 V after docking versus
            # 25.71-26.19 V while moving or resting. Remember docked state so
            # it survives the charger's later maintenance/idle phase.
            self._docked_latched = True
            if (
                self._last_action == "5"
                and not self._low_battery_seen_since_auto
                and not self._alarm_seen_since_auto
            ):
                # The mower returned and began charging without Y7/Y0 and
                # without a low-battery/alarm indication. The wet-sensor test
                # demonstrated this exact sequence. It remains explicitly
                # labelled inferred because schedule completion can look alike.
                self._rain_detected_inferred = True
            self._activity = "docked"
            source = "charging_voltage"
        elif self._docked_latched:
            self._activity = "docked"
            source = "remembered_dock"

        return replace(
            status,
            inferred_activity=self._activity,
            docked=self._docked_latched,
            charging=charging,
            rain_detected_inferred=self._rain_detected_inferred,
            inference_source=source,
        )

    async def async_close(self) -> None:
        """Close the client and stop heartbeat."""
        async with self._lock:
            await self._async_reset_locked()

    async def _async_connect_locked(self) -> bool:
        if self._writer is not None and not self._writer.is_closing():
            return False
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT):
                self._reader, self._writer = await asyncio.open_connection(
                    self.host, self.port
                )
        except (OSError, asyncio.TimeoutError) as error:
            raise LyfcoConnectionError(
                f"Unable to connect to {self.host}:{self.port}"
            ) from error
        self._heartbeat_task = asyncio.create_task(
            self._async_heartbeat_loop(), name=f"lyfco-heartbeat-{self.host}"
        )
        return True

    async def _async_send_locked(self, payload: str) -> None:
        if self._writer is None or self._writer.is_closing():
            raise LyfcoConnectionError("TCP connection is closed")
        self._writer.write(build_vsp(payload))
        await self._writer.drain()

    async def _async_send_command_locked(self, mark: str, body: str = "") -> None:
        await self._async_send_locked(_uart_envelope(build_command(mark, body)))

    async def _async_read_frame_locked(self) -> str:
        if self._reader is None:
            raise LyfcoConnectionError("TCP connection is closed")
        try:
            while True:
                # Some mower commands produce a short, unframed acknowledgement.
                # Search for the next VSP marker instead of assuming that the
                # stream is already positioned exactly at a frame boundary.
                marker = bytearray()
                while bytes(marker) != b"0h":
                    marker.append((await self._reader.readexactly(1))[0])
                    if len(marker) > 2:
                        del marker[0]
                header = b"0h" + await self._reader.readexactly(2)
                frame_length = int.from_bytes(header[2:4], "big")
                if frame_length < VSP_HEADER_SIZE or frame_length > 65535:
                    continue
                remainder = await self._reader.readexactly(frame_length - 4)
                return decode_vsp(header + remainder)
        except (asyncio.IncompleteReadError, ConnectionError) as error:
            # The Miotlink bridge occasionally closes an otherwise healthy
            # persistent connection. Convert EOF into our connection error so
            # the caller resets the stream and retries on a fresh socket.
            raise LyfcoConnectionError(
                "Mower closed the TCP connection while a frame was being read"
            ) from error

    async def _async_wait_for_status_locked(self) -> MowerStatus:
        deadline = time.monotonic() + RESPONSE_TIMEOUT
        while (remaining := deadline - time.monotonic()) > 0:
            async with asyncio.timeout(remaining):
                message = await self._async_read_frame_locked()
            if message.startswith("CodeName=SearchAck"):
                model, firmware = parse_search_ack(message)
                self._model = model or self._model
                self._firmware = firmware or self._firmware
                continue
            uart = _extract_uart(message)
            if uart is None or not verify_command(uart):
                continue
            if uart[4:5] == "V" and len(uart) >= 8:
                self._firmware = f"v{uart[5]}.{uart[6]}.{uart[7]}"
                continue
            if uart[4:5] == "O":
                self._parse_pin_status(uart)
                continue
            if uart[4:5] == "W":
                return parse_status(uart, self._firmware, self._model)
        raise LyfcoConnectionError("Timed out waiting for W status response")

    async def _async_collect_read_group_locked(
        self, queries: tuple[tuple[str, str], ...]
    ) -> list[str]:
        """Send a small read group and collect verified matching replies."""
        expected = {
            (mark, body[:1] if mark == "S" else "") for mark, body in queries
        }
        commands: list[str] = []
        for mark, body in queries:
            await self._async_send_command_locked(mark, body)
            await asyncio.sleep(0.15)

        deadline = time.monotonic() + EXTENDED_RESPONSE_TIMEOUT
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                async with asyncio.timeout(remaining):
                    message = await self._async_read_frame_locked()
            except asyncio.TimeoutError:
                break
            uart = _extract_uart(message)
            if uart is None or not verify_command(uart):
                continue
            mark = uart[4:5]
            body = uart[5 : int(uart[2:4]) - 2]
            key = (mark, body[:1] if mark == "S" else "")
            if key not in expected:
                continue
            if uart not in commands:
                commands.append(uart)
            if mark == "R":
                area_numbers = {
                    command[5:6] for command in commands if command[4:5] == "R"
                }
                if area_numbers == set("123456"):
                    break
            elif all(
                any(
                    command[4:5] == expected_mark
                    and (
                        expected_mark != "S"
                        or command[5:6] == expected_body
                    )
                    for command in commands
                )
                for expected_mark, expected_body in expected
            ):
                break
        return commands

    async def _async_refresh_extended_locked(self) -> None:
        """Refresh read-only working-area and weekly-schedule caches."""
        for command in await self._async_collect_read_group_locked((("F", ""),)):
            with suppress(LyfcoProtocolError):
                self._configuration = parse_configuration(command)

        area_commands = await self._async_collect_read_group_locked((("R", ""),))
        areas = {area.number: area for area in self._areas}
        for command in area_commands:
            with suppress(LyfcoProtocolError):
                area = parse_area(command)
                areas[area.number] = area
        if len(areas) < 6:
            for command in await self._async_collect_read_group_locked((("R", ""),)):
                with suppress(LyfcoProtocolError):
                    area = parse_area(command)
                    areas[area.number] = area
        self._areas = tuple(areas[number] for number in sorted(areas))

        schedules = {schedule.day: schedule for schedule in self._schedules}
        for first_day in range(0, 7, 2):
            queries = tuple(
                ("S", str(day)) for day in range(first_day, min(first_day + 2, 7))
            )
            for command in await self._async_collect_read_group_locked(queries):
                with suppress(LyfcoProtocolError):
                    schedule = parse_schedule(command)
                    schedules[schedule.day] = schedule
        for day in range(7):
            if day in schedules:
                continue
            for command in await self._async_collect_read_group_locked(
                (("S", str(day)),)
            ):
                with suppress(LyfcoProtocolError):
                    schedule = parse_schedule(command)
                    schedules[schedule.day] = schedule
        self._schedules = tuple(schedules[day] for day in sorted(schedules))

        # Even a partial/failed optional read is rate-limited so the mower's
        # slow UART bridge is never flooded every 30 seconds.
        self._extended_refreshed_at = time.monotonic()

    async def _async_wait_for_pin_status_locked(self) -> None:
        deadline = time.monotonic() + RESPONSE_TIMEOUT
        while (remaining := deadline - time.monotonic()) > 0:
            async with asyncio.timeout(remaining):
                message = await self._async_read_frame_locked()
            uart = _extract_uart(message)
            if uart is None or not verify_command(uart):
                continue
            if uart[4:5] == "O":
                self._parse_pin_status(uart)
                return
            if uart[4:5] == "V" and len(uart) >= 8:
                self._firmware = f"v{uart[5]}.{uart[6]}.{uart[7]}"
        raise LyfcoConnectionError("Timed out waiting for PIN status response")

    def _parse_pin_status(self, command: str) -> None:
        if not verify_command(command) or command[4:5] != "O":
            raise LyfcoProtocolError("Invalid PIN status response")
        declared_length = int(command[2:4])
        body = command[5 : declared_length - 2]
        if len(body) != 5 or not body.isdigit():
            raise LyfcoProtocolError("Unsupported PIN status response")
        self._pin_enabled = body[4] == "1"
        self._pin_checked = True

    async def _async_heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                async with self._lock:
                    await self._async_send_locked("CodeName=Search")
        except asyncio.CancelledError:
            raise
        except (OSError, LyfcoError) as error:
            _LOGGER.debug("Lyfco heartbeat failed: %s", error)
            writer = self._writer
            self._reader = None
            self._writer = None
            if writer is not None:
                writer.close()

    async def _async_reset_locked(self) -> None:
        heartbeat = self._heartbeat_task
        self._heartbeat_task = None
        if heartbeat is not None and heartbeat is not asyncio.current_task():
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
        writer = self._writer
        self._reader = None
        self._writer = None
        self._pin_checked = False
        self._pin_enabled = False
        if writer is not None:
            writer.close()
            with suppress(OSError, asyncio.TimeoutError):
                async with asyncio.timeout(2):
                    await writer.wait_closed()
