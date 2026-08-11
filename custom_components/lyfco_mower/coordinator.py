"""Data coordinator for Lyfco mower."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CHARGE_RECONNECT_VOLTAGE,
    CHARGING_VOLTAGE,
    DOMAIN,
    POLL_INTERVAL,
    VOLTAGE_TREND_EPSILON,
)
from .protocol import LyfcoError, LyfcoMowerClient, MowerSchedule, MowerStatus

_LOGGER = logging.getLogger(__name__)

WEEK_MINUTES = 7 * 24 * 60
SCHEDULE_ONLINE_MAX_AGE = 75.0
SCHEDULE_START_GRACE = 120.0
SCHEDULE_DEPARTURE_CONFIRMATIONS = 2
SCHEDULE_REDOCK_RISE = 0.15


def _mower_weekday(local_time: datetime) -> int:
    """Return the mower weekday number where Sunday is 0."""
    return (local_time.weekday() + 1) % 7


def _schedule_state(
    schedules: tuple[MowerSchedule, ...], local_time: datetime
) -> tuple[bool, bool, bool]:
    """Return active, starts-now and ends-now for the configured weekly schedule."""
    now_week_minute = (
        _mower_weekday(local_time) * 24 * 60
        + local_time.hour * 60
        + local_time.minute
    )
    active = False
    starts_now = False
    ends_now = False

    for schedule in schedules:
        duration = sum(schedule.area_minutes)
        if duration <= 0:
            continue
        hour, minute = (int(part) for part in schedule.start_time.split(":"))
        start_week_minute = schedule.day * 24 * 60 + hour * 60 + minute
        end_week_minute = (start_week_minute + duration) % WEEK_MINUTES
        elapsed = (now_week_minute - start_week_minute) % WEEK_MINUTES
        active |= elapsed < duration
        starts_now |= now_week_minute == start_week_minute
        ends_now |= now_week_minute == end_week_minute

    return active, starts_now, ends_now


class LyfcoCoordinator(DataUpdateCoordinator[MowerStatus]):
    """Poll status while adding Home Assistant schedule-aware activity inference."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: LyfcoMowerClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=POLL_INTERVAL,
        )
        self.client = client
        self._clock_signature: tuple[object, ...] | None = None
        self._last_clock_attempt = 0.0
        self._consecutive_update_failures = 0
        self._last_real_success = 0.0
        self._schedule_unsub: Callable[[], None] | None = None
        self._schedule_override: str | None = None
        self._schedule_override_started = 0.0
        self._schedule_departure_samples = 0
        self._schedule_departure_evidence = False
        self._schedule_seen_below_dock_voltage = False
        self._schedule_was_active = False
        self._previous_voltage: float | None = None

    async def _async_update_data(self) -> MowerStatus:
        local_time = dt_util.now()
        try:
            status = await self.client.async_get_status(local_time)
        except LyfcoError as error:
            self._consecutive_update_failures += 1
            if self.data is not None and self._consecutive_update_failures <= 2:
                # Preserve the last confirmed state for two transient bridge
                # failures, but do not refresh _last_real_success. Schedule
                # clock events therefore cannot mistake cached data for an
                # online mower.
                _LOGGER.debug(
                    "Keeping last mower status after transient failure %s/2: %s",
                    self._consecutive_update_failures,
                    error,
                )
                return self.data
            raise UpdateFailed(str(error)) from error

        self._consecutive_update_failures = 0
        self._last_real_success = time.monotonic()
        status = self._apply_schedule_overlay(status, local_time)
        await self.async_sync_clock()
        return status

    def async_start_schedule_tracker(self) -> None:
        """Track local minute boundaries used by the mower's internal schedule."""
        if self._schedule_unsub is not None:
            return
        self._schedule_unsub = async_track_time_change(
            self.hass,
            self._handle_schedule_minute,
            second=0,
        )

    def async_stop_schedule_tracker(self) -> None:
        """Stop the Home Assistant schedule clock listener."""
        if self._schedule_unsub is None:
            return
        self._schedule_unsub()
        self._schedule_unsub = None

    @callback
    def _handle_schedule_minute(self, local_time: datetime) -> None:
        """Schedule asynchronous handling at each local minute boundary."""
        self.hass.async_create_task(self._async_handle_schedule_minute(local_time))

    async def _async_handle_schedule_minute(self, local_time: datetime) -> None:
        """Apply an exact schedule start/end only when the mower is recently online."""
        status = self.data
        if status is None or any(status.alarm_flags):
            return
        if (
            self._last_real_success <= 0
            or time.monotonic() - self._last_real_success > SCHEDULE_ONLINE_MAX_AGE
        ):
            return

        active, starts_now, ends_now = _schedule_state(status.schedules, local_time)

        # A simultaneous end/start (for overlapping schedule rows) is treated
        # as an active start, because the mower is still expected to work.
        if starts_now:
            self._schedule_override = "mowing"
            self._schedule_override_started = time.monotonic()
            self._schedule_departure_samples = 0
            self._schedule_departure_evidence = False
            self._schedule_seen_below_dock_voltage = False
            self._schedule_was_active = True
            self.async_set_updated_data(
                replace(
                    status,
                    inferred_activity="mowing",
                    docked=False,
                    charging=False,
                    inference_source="schedule_clock_start",
                )
            )
            return

        if ends_now:
            self._schedule_was_active = active
            self._schedule_departure_samples = 0
            self._schedule_departure_evidence = False
            self._schedule_seen_below_dock_voltage = False
            if status.docked:
                self._schedule_override = None
                return
            self._schedule_override = "returning"
            self._schedule_override_started = time.monotonic()
            self.async_set_updated_data(
                replace(
                    status,
                    inferred_activity="returning",
                    docked=False,
                    charging=False,
                    inference_source="schedule_clock_end",
                )
            )

    def _apply_schedule_overlay(
        self, status: MowerStatus, local_time: datetime
    ) -> MowerStatus:
        """Combine schedule expectation with real alarms and measured dock behavior."""
        active, _starts_now, _ends_now = _schedule_state(
            status.schedules, local_time
        )
        was_active = self._schedule_was_active
        self._schedule_was_active = active

        previous_voltage = self._previous_voltage
        voltage_delta = (
            None if previous_voltage is None else status.voltage - previous_voltage
        )
        self._previous_voltage = status.voltage

        # Real alarms always outrank schedule inference.
        if any(status.alarm_flags):
            self._schedule_override = None
            self._schedule_departure_samples = 0
            self._schedule_departure_evidence = False
            self._schedule_seen_below_dock_voltage = False
            return status

        # An explicit Home Assistant pause/home command outranks the schedule.
        if (
            status.inference_source == "last_command"
            and status.inferred_activity in {"paused", "returning"}
        ):
            self._schedule_override = None
            self._schedule_departure_samples = 0
            return status

        if self._schedule_override == "returning":
            # Measured docking wins as soon as the mower reaches the charger.
            if status.docked:
                self._schedule_override = None
                return status
            return replace(
                status,
                inferred_activity="returning",
                docked=False,
                charging=False,
                inference_source="schedule_clock_end",
            )

        if active:
            if self._schedule_override == "mowing":
                # Evidence that the mower really left the dock. A falling sample
                # at/below the measured 28.6 V reconnect level is enough to keep
                # the scheduled-start assumption alive; falling below 26.4 V is
                # stronger evidence and arms detection of the next real dock.
                if (
                    voltage_delta is not None
                    and status.voltage <= CHARGE_RECONNECT_VOLTAGE
                    and voltage_delta <= -VOLTAGE_TREND_EPSILON
                ):
                    self._schedule_departure_evidence = True
                if status.voltage < CHARGING_VOLTAGE:
                    self._schedule_departure_evidence = True
                    self._schedule_seen_below_dock_voltage = True

                # After a confirmed departure, a fresh voltage rise back into
                # the charging range means the mower has returned during the
                # still-active schedule. Real docking then outranks "mowing".
                crossed_back_to_dock = (
                    self._schedule_seen_below_dock_voltage
                    and previous_voltage is not None
                    and previous_voltage < CHARGING_VOLTAGE <= status.voltage
                )
                strong_redock_rise = (
                    self._schedule_departure_evidence
                    and status.docked
                    and voltage_delta is not None
                    and voltage_delta >= SCHEDULE_REDOCK_RISE
                    and status.voltage >= CHARGE_RECONNECT_VOLTAGE
                )
                if crossed_back_to_dock or strong_redock_rise:
                    self._schedule_override = None
                    self._schedule_departure_samples = 0
                    return replace(
                        status,
                        inferred_activity="docked",
                        docked=True,
                        inference_source="schedule_midrun_dock",
                    )

                # If the expected schedule start never produces any departure
                # evidence, stop pretending after two minutes and trust the
                # measured dock state (for example if the mower refused to run).
                if (
                    not self._schedule_departure_evidence
                    and status.docked
                    and time.monotonic() - self._schedule_override_started
                    >= SCHEDULE_START_GRACE
                ):
                    self._schedule_override = None
                    return status

                return replace(
                    status,
                    inferred_activity="mowing",
                    docked=False,
                    charging=False,
                    inference_source="schedule_clock_active",
                )

            if status.docked:
                # During an active schedule the mower may go home for charging.
                # Its normal dock maintenance also falls through 28.6 V before
                # reconnecting. Require two consecutive falling samples at or
                # below that level before calling it a genuine departure/resume.
                if (
                    voltage_delta is not None
                    and status.voltage <= CHARGE_RECONNECT_VOLTAGE
                    and voltage_delta <= -VOLTAGE_TREND_EPSILON
                ):
                    self._schedule_departure_samples += 1
                elif (
                    voltage_delta is not None
                    and voltage_delta >= VOLTAGE_TREND_EPSILON
                ) or status.voltage > CHARGE_RECONNECT_VOLTAGE:
                    self._schedule_departure_samples = 0

                if self._schedule_departure_samples >= SCHEDULE_DEPARTURE_CONFIRMATIONS:
                    self._schedule_override = "mowing"
                    self._schedule_override_started = time.monotonic()
                    self._schedule_departure_samples = 0
                    self._schedule_departure_evidence = True
                    self._schedule_seen_below_dock_voltage = (
                        status.voltage < CHARGING_VOLTAGE
                    )
                    return replace(
                        status,
                        inferred_activity="mowing",
                        docked=False,
                        charging=False,
                        inference_source="schedule_resume_voltage",
                    )
                return status

            self._schedule_departure_samples = 0
            return replace(
                status,
                inferred_activity="mowing",
                docked=False,
                charging=False,
                inference_source="schedule_clock_active",
            )

        # If Home Assistant happened to miss the exact minute event but saw the
        # active schedule on the previous real poll, retain a sensible return
        # state until measured docking takes over.
        if was_active and not status.docked:
            self._schedule_override = "returning"
            self._schedule_override_started = time.monotonic()
            return replace(
                status,
                inferred_activity="returning",
                docked=False,
                charging=False,
                inference_source="schedule_elapsed",
            )

        if self._schedule_override == "mowing":
            self._schedule_override = None
        self._schedule_departure_samples = 0
        self._schedule_departure_evidence = False
        self._schedule_seen_below_dock_voltage = False
        return status

    async def async_sync_clock(self, force: bool = False) -> bool:
        """Sync on startup, local date change, or DST/time-zone change."""
        local_time = dt_util.now()
        signature = (
            local_time.date(),
            local_time.utcoffset(),
            str(local_time.tzinfo),
        )
        if not force and signature == self._clock_signature:
            return True
        if not force and time.monotonic() - self._last_clock_attempt < 300:
            return False
        self._last_clock_attempt = time.monotonic()
        try:
            await self.client.async_sync_clock(local_time)
        except LyfcoError as error:
            _LOGGER.warning("Could not synchronize mower clock: %s", error)
            return False
        self._clock_signature = signature
        return True
