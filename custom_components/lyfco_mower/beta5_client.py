"""Beta.5 protocol wrapper.

The wire protocol remains in protocol.py. This wrapper adds command revision
tracking for the coordinator and tolerant schedule read-back verification.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime
import time
from typing import Any

from .protocol import (
    LyfcoConnectionError,
    LyfcoError,
    LyfcoMowerClient,
    LyfcoProtocolError,
    MowerSchedule,
    MowerStatus,
    parse_schedule,
)


class Beta5MowerClient(LyfcoMowerClient):
    """Keep protocol transport stable while exposing beta.5 coordination hooks."""

    SCHEDULE_READBACK_ATTEMPTS = 3
    USER_COMMAND_FRESHNESS_SECONDS = 60.0

    def __init__(self, host: str, port: int = 9600) -> None:
        super().__init__(host, port)
        self._command_revision = 0
        self._last_command_mono: float | None = None
        self._last_schedule_write: dict[str, Any] | None = None
        self._last_status_mono: float | None = None

    @property
    def command_revision(self) -> int:
        return self._command_revision

    @property
    def last_action(self) -> str | None:
        return self._last_action

    @property
    def last_action_age_seconds(self) -> float | None:
        if self._last_command_mono is None:
            return None
        return max(0.0, time.monotonic() - self._last_command_mono)

    @property
    def schedule_write_diagnostics(self) -> dict[str, Any] | None:
        return self._last_schedule_write

    @property
    def last_status_age_seconds(self) -> float | None:
        if self._last_status_mono is None:
            return None
        return max(0.0, time.monotonic() - self._last_status_mono)

    async def async_get_status(
        self, local_time: datetime | None = None
    ) -> MowerStatus:
        status = await super().async_get_status(local_time)
        self._last_status_mono = time.monotonic()
        return status

    async def _async_send_action(self, action: str, description: str) -> None:
        """Refresh stale status before a user movement command."""
        age = self.last_status_age_seconds
        if age is None or age > self.USER_COMMAND_FRESHNESS_SECONDS:
            try:
                await self.async_get_status()
            except LyfcoError as error:
                raise LyfcoConnectionError(
                    "Cannot send mower command because no fresh status was "
                    "available within the last 60 seconds"
                ) from error
        await super()._async_send_action(action, description)

    def _record_action(self, action: str) -> None:
        super()._record_action(action)
        self._command_revision += 1
        self._last_command_mono = time.monotonic()

    async def async_set_schedule(
        self,
        day: int,
        start_hour: int,
        start_minute: int,
        edge_mowing: bool,
        area_minutes: tuple[int, int, int, int, int, int],
    ) -> MowerSchedule:
        """Write one weekday schedule and tolerate delayed mower read-back."""
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
        requested = {
            "day": day,
            "edge_mowing": edge_mowing,
            "start_time": expected.start_time,
            "area_minutes": list(area_minutes),
        }
        readbacks: list[dict[str, Any]] = []
        self._last_schedule_write = {
            "requested": requested,
            "attempts": readbacks,
            "result": "in_progress",
        }

        async with self._lock:
            new_connection = await self._async_connect_locked()
            if new_connection:
                await self._async_send_locked("CodeName=Search")
            await self._async_send_command_locked("S", body)

            # The mower occasionally acknowledges the write internally before
            # its next S read reflects the new value. Give it one short settle
            # delay, then retry read-only verification rather than rewriting.
            await asyncio.sleep(0.30)
            last_confirmed: MowerSchedule | None = None

            for attempt in range(1, self.SCHEDULE_READBACK_ATTEMPTS + 1):
                if attempt > 1:
                    await asyncio.sleep(0.35)

                commands = await self._async_collect_read_group_locked(
                    (("S", str(day)),)
                )
                confirmed_this_attempt: MowerSchedule | None = None
                for command in commands:
                    with suppress(LyfcoProtocolError):
                        schedule = parse_schedule(command)
                        if schedule.day == day:
                            confirmed_this_attempt = schedule
                            last_confirmed = schedule

                readbacks.append(
                    {
                        "attempt": attempt,
                        "received": None
                        if confirmed_this_attempt is None
                        else {
                            "day": confirmed_this_attempt.day,
                            "edge_mowing": confirmed_this_attempt.edge_mowing,
                            "start_time": confirmed_this_attempt.start_time,
                            "area_minutes": list(
                                confirmed_this_attempt.area_minutes
                            ),
                        },
                        "matched": confirmed_this_attempt == expected,
                    }
                )

                if confirmed_this_attempt == expected:
                    schedules = {
                        schedule.day: schedule for schedule in self._schedules
                    }
                    schedules[day] = confirmed_this_attempt
                    self._schedules = tuple(
                        schedules[index] for index in sorted(schedules)
                    )
                    self._last_schedule_write["result"] = "verified"
                    self._last_schedule_write["verified_on_attempt"] = attempt
                    return confirmed_this_attempt

        if last_confirmed is None:
            self._last_schedule_write["result"] = "no_readback"
            raise LyfcoConnectionError(
                "The mower did not return the written schedule after "
                f"{self.SCHEDULE_READBACK_ATTEMPTS} verification attempts"
            )

        self._last_schedule_write["result"] = "mismatch"
        raise LyfcoProtocolError(
            "The schedule read back from the mower did not match the requested "
            f"values after {self.SCHEDULE_READBACK_ATTEMPTS} verification attempts"
        )
