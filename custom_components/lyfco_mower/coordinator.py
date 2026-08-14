"""Data coordinator for Lyfco mower beta.5."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .beta5_client import Beta5MowerClient
from .beta5_state import Beta5FinalStateMachine
from .const import DOMAIN
from .protocol import LyfcoError, MowerSchedule, MowerStatus

_LOGGER = logging.getLogger(__name__)

NORMAL_POLL_SECONDS = 30
FAST_POLL_SECONDS = 10
FAST_POLL_WINDOW_SECONDS = 180
SCHEDULE_ONLINE_MAX_AGE = 75.0
WEEK_MINUTES = 7 * 24 * 60

STORAGE_VERSION = 1


def _mower_weekday(local_time: datetime) -> int:
    """Return mower weekday number where Sunday is 0."""
    return (local_time.weekday() + 1) % 7


def _schedule_state(
    schedules: tuple[MowerSchedule, ...], local_time: datetime
) -> tuple[bool, bool, bool]:
    """Return active, starts-now and ends-now for the weekly schedule."""
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
    """Poll mower data and produce the single beta.5 final activity state."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: Beta5MowerClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=NORMAL_POLL_SECONDS),
        )
        self.client = client
        self.state_machine = Beta5FinalStateMachine()

        self._clock_signature: tuple[object, ...] | None = None
        self._last_clock_attempt = 0.0

        self._consecutive_update_failures = 0
        self._last_real_success = 0.0
        self._connection_state = "starting"

        self._schedule_unsub: Callable[[], None] | None = None
        self._schedule_was_active = False

        self._command_revision_seen = client.command_revision

        self._fast_poll_until = 0.0
        self._fast_poll_reason: str | None = None

        self._store: Store[dict[str, Any]] = Store(
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}.beta5.{entry.entry_id}",
        )
        self._learning_loaded = False

    async def async_load_persistent_state(self) -> None:
        """Load completed charge/mowing history retained across reloads."""
        try:
            stored = await self._store.async_load()
        except Exception as error:  # storage failure must not block mower setup
            _LOGGER.warning("Could not load Lyfco beta.5 state history: %s", error)
            return
        if isinstance(stored, dict):
            self.state_machine.load_persistent(stored)
            self._learning_loaded = True

    async def async_shutdown(self) -> None:
        """Persist diagnostic history before unload."""
        try:
            await self._store.async_save(self.state_machine.export_persistent())
        except Exception as error:
            _LOGGER.debug("Could not save Lyfco beta.5 state history: %s", error)

    async def _async_update_data(self) -> MowerStatus:
        now_mono = time.monotonic()
        self._update_poll_mode(now_mono)
        local_time = dt_util.now()

        try:
            raw_status = await self.client.async_get_status(local_time)
        except LyfcoError as error:
            self._consecutive_update_failures += 1
            self._connection_state = "recovering"
            if self.data is not None and self._consecutive_update_failures <= 2:
                _LOGGER.debug(
                    "Keeping last mower status after transient failure %s/2: %s",
                    self._consecutive_update_failures,
                    error,
                )
                return self.data
            raise UpdateFailed(str(error)) from error

        self._consecutive_update_failures = 0
        self._last_real_success = now_mono
        self._connection_state = "connected"

        now_utc = datetime.now(timezone.utc)

        if self.client.command_revision != self._command_revision_seen:
            self._command_revision_seen = self.client.command_revision
            if self.state_machine.note_command(
                self.client.last_action,
                now_mono=now_mono,
                now_utc=now_utc,
                voltage=raw_status.voltage,
            ):
                self._enter_fast_poll("verified_command")

        active, starts_now, ends_now = _schedule_state(
            raw_status.schedules, local_time
        )
        was_active = self._schedule_was_active
        schedule_started = starts_now
        schedule_ended = ends_now or (was_active and not active)
        self._schedule_was_active = active

        state_changed = self.state_machine.update(
            voltage=raw_status.voltage,
            alarm_flags=raw_status.alarm_flags,
            raw_docked=raw_status.docked,
            raw_charging=raw_status.charging,
            schedule_active=active,
            schedule_started=schedule_started,
            schedule_ended=schedule_ended,
            now_mono=now_mono,
            now_utc=now_utc,
        )
        if state_changed:
            self._enter_fast_poll("state_transition")

        final_status = replace(
            raw_status,
            inferred_activity=self.state_machine.activity,
            docked=self.state_machine.docked,
            charging=self.state_machine.charging,
            rain_detected_inferred=(
                raw_status.rain_detected_inferred and self.state_machine.docked
            ),
            inference_source=self.state_machine.source,
        )

        if self.state_machine.pop_persistent_dirty():
            try:
                await self._store.async_save(
                    self.state_machine.export_persistent()
                )
            except Exception as error:
                _LOGGER.debug("Could not persist Lyfco beta.5 history: %s", error)

        await self.async_sync_clock()
        return final_status

    def _enter_fast_poll(self, reason: str) -> None:
        """Poll at 10 s for three minutes after meaningful activity."""
        self._fast_poll_until = max(
            self._fast_poll_until,
            time.monotonic() + FAST_POLL_WINDOW_SECONDS,
        )
        self._fast_poll_reason = reason
        self.update_interval = timedelta(seconds=FAST_POLL_SECONDS)

    def _update_poll_mode(self, now_mono: float) -> None:
        if now_mono < self._fast_poll_until:
            self.update_interval = timedelta(seconds=FAST_POLL_SECONDS)
            return
        self.update_interval = timedelta(seconds=NORMAL_POLL_SECONDS)
        self._fast_poll_reason = None

    def async_start_schedule_tracker(self) -> None:
        """Track exact minute boundaries without optimistically forcing state."""
        if self._schedule_unsub is not None:
            return
        self._schedule_unsub = async_track_time_change(
            self.hass,
            self._handle_schedule_minute,
            second=0,
        )

    def async_stop_schedule_tracker(self) -> None:
        if self._schedule_unsub is None:
            return
        self._schedule_unsub()
        self._schedule_unsub = None

    @callback
    def _handle_schedule_minute(self, local_time: datetime) -> None:
        self.hass.async_create_task(self._async_handle_schedule_minute(local_time))

    async def _async_handle_schedule_minute(self, local_time: datetime) -> None:
        """Refresh promptly at a schedule boundary; do not invent movement."""
        status = self.data
        if status is None or any(status.alarm_flags):
            return
        if (
            self._last_real_success <= 0
            or time.monotonic() - self._last_real_success > SCHEDULE_ONLINE_MAX_AGE
        ):
            return

        _active, starts_now, ends_now = _schedule_state(
            status.schedules, local_time
        )
        if starts_now or ends_now:
            self._enter_fast_poll(
                "schedule_start" if starts_now else "schedule_end"
            )
            await self.async_request_refresh()

    @property
    def current_charging_minutes(self) -> float:
        return self.state_machine.current_charging_minutes(time.monotonic())

    @property
    def current_mowing_minutes(self) -> float:
        return self.state_machine.current_mowing_minutes(time.monotonic())

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

    def diagnostics(self) -> dict[str, Any]:
        """Return beta.5 coordinator diagnostics without network credentials."""
        now_mono = time.monotonic()
        return {
            "connection_state": self._connection_state,
            "last_real_status_age_seconds": (
                None
                if self._last_real_success <= 0
                else round(max(0.0, now_mono - self._last_real_success), 1)
            ),
            "consecutive_update_failures": self._consecutive_update_failures,
            "poll_mode": (
                "fast" if now_mono < self._fast_poll_until else "normal"
            ),
            "current_poll_interval_seconds": (
                FAST_POLL_SECONDS
                if now_mono < self._fast_poll_until
                else NORMAL_POLL_SECONDS
            ),
            "fast_poll_remaining_seconds": round(
                max(0.0, self._fast_poll_until - now_mono), 1
            ),
            "fast_poll_reason": self._fast_poll_reason,
            "learning_loaded": self._learning_loaded,
            "schedule_was_active": self._schedule_was_active,
            "command_revision_seen": self._command_revision_seen,
            "last_action_age_seconds": self.client.last_action_age_seconds,
        }
