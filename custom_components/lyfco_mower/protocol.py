"""Local TCP protocol for Lyfco/Miotlink robot mowers."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
import logging
import time

from .const import ALARM_KEYS, DEFAULT_PORT

_LOGGER = logging.getLogger(__name__)

VSP_HEADER_SIZE = 20
CONNECT_TIMEOUT = 10.0
RESPONSE_TIMEOUT = 6.0
HEARTBEAT_INTERVAL = 5.0


class LyfcoError(Exception):
    """Base error for Lyfco communication."""


class LyfcoConnectionError(LyfcoError):
    """The mower could not be reached."""


class LyfcoProtocolError(LyfcoError):
    """The mower returned an invalid or unsupported response."""


@dataclass(frozen=True, slots=True)
class MowerStatus:
    """Decoded status returned by the mower."""

    runtime_hours: int
    charge_hours: int
    voltage: float
    alarm_flags: tuple[bool, ...]
    firmware: str | None = None

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


def parse_status(command: str, firmware: str | None = None) -> MowerStatus:
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
        self._pin_checked = False
        self._pin_enabled = False

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
                    return await self._async_wait_for_status_locked()
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

    async def _async_wait_for_status_locked(self) -> MowerStatus:
        deadline = time.monotonic() + RESPONSE_TIMEOUT
        while (remaining := deadline - time.monotonic()) > 0:
            async with asyncio.timeout(remaining):
                message = await self._async_read_frame_locked()
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
                return parse_status(uart, self._firmware)
        raise LyfcoConnectionError("Timed out waiting for W status response")

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
