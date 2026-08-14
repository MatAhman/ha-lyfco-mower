"""Final beta.5 state machine with schedule-departure evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .state_machine import (
    Beta5StateMachine,
    CHARGE_RECONNECT_REFERENCE,
    VOLTAGE_EPSILON,
)

SCHEDULE_START_GRACE_SECONDS = 120.0
SCHEDULE_DEPARTURE_CONFIRMATIONS = 2
SCHEDULE_DEPARTURE_STRONG_VOLTAGE = 26.4


class Beta5FinalStateMachine(Beta5StateMachine):
    """Add conservative physical departure proof at a scheduled start."""

    def __init__(self) -> None:
        super().__init__()
        self.schedule_departure_pending = False
        self.schedule_departure_started_mono: float | None = None
        self.schedule_departure_samples = 0
        self.schedule_departure_baseline_voltage: float | None = None
        self.schedule_departure_evidence = False
        self._schedule_departure_previous_voltage: float | None = None

    def update(
        self,
        *,
        voltage: float,
        alarm_flags: tuple[bool, ...],
        raw_docked: bool,
        raw_charging: bool,
        schedule_active: bool,
        schedule_started: bool,
        schedule_ended: bool,
        now_mono: float,
        now_utc: datetime,
    ) -> bool:
        """Arm a departure probe instead of blindly clearing a real dock."""
        changed = False
        suppress_schedule_start = False

        if schedule_started and self.docked:
            self.schedule_departure_pending = True
            self.schedule_departure_started_mono = now_mono
            self.schedule_departure_samples = 0
            self.schedule_departure_baseline_voltage = voltage
            self.schedule_departure_evidence = False
            self._schedule_departure_previous_voltage = voltage
            # Keep Docked until voltage behavior shows the mower really left.
            suppress_schedule_start = True

        if self.schedule_departure_pending:
            previous = self._schedule_departure_previous_voltage
            delta = None if previous is None else voltage - previous
            self._schedule_departure_previous_voltage = voltage

            if (
                delta is not None
                and voltage <= CHARGE_RECONNECT_REFERENCE
                and delta <= -VOLTAGE_EPSILON
            ):
                self.schedule_departure_samples += 1
            elif delta is not None and delta >= VOLTAGE_EPSILON:
                self.schedule_departure_samples = 0

            strong_departure = (
                delta is not None
                and delta <= -VOLTAGE_EPSILON
                and voltage < SCHEDULE_DEPARTURE_STRONG_VOLTAGE
            )
            baseline_drop = (
                0.0
                if self.schedule_departure_baseline_voltage is None
                else self.schedule_departure_baseline_voltage - voltage
            )
            confirmed_departure = strong_departure or (
                self.schedule_departure_samples
                >= SCHEDULE_DEPARTURE_CONFIRMATIONS
                and baseline_drop >= 0.10
            )

            if schedule_active and confirmed_departure:
                self.schedule_departure_evidence = True
                self.schedule_departure_pending = False
                self.return_context_active = False
                self.return_context_reason = None
                self.return_started_mono = None
                self._explicit_hold = False
                # Discard pre-departure charging samples so residual charging
                # history cannot immediately re-dock the mower.
                self._samples.clear()
                changed |= self._set_state(
                    "mowing",
                    "mowing",
                    False,
                    False,
                    "schedule_departure_voltage",
                    now_mono,
                    now_utc,
                    voltage,
                )
            elif (
                schedule_ended
                or not schedule_active
                or (
                    self.schedule_departure_started_mono is not None
                    and now_mono - self.schedule_departure_started_mono
                    >= SCHEDULE_START_GRACE_SECONDS
                )
            ):
                # No physical departure within the grace window: retain dock.
                self.schedule_departure_pending = False
                self.schedule_departure_samples = 0
                self._schedule_departure_previous_voltage = None

        changed |= super().update(
            voltage=voltage,
            alarm_flags=alarm_flags,
            raw_docked=raw_docked,
            raw_charging=raw_charging,
            schedule_active=schedule_active,
            schedule_started=(schedule_started and not suppress_schedule_start),
            schedule_ended=schedule_ended,
            now_mono=now_mono,
            now_utc=now_utc,
        )
        if self.activity == "mowing" and self.schedule_departure_evidence:
            self.source = "schedule_departure_voltage"
        return changed

    def diagnostics(self, now_mono: float) -> dict[str, Any]:
        result = super().diagnostics(now_mono)
        result["schedule_departure"] = {
            "pending": self.schedule_departure_pending,
            "samples": self.schedule_departure_samples,
            "evidence": self.schedule_departure_evidence,
            "baseline_voltage": self.schedule_departure_baseline_voltage,
            "grace_seconds": SCHEDULE_START_GRACE_SECONDS,
            "confirmations": SCHEDULE_DEPARTURE_CONFIRMATIONS,
            "strong_voltage_reference": SCHEDULE_DEPARTURE_STRONG_VOLTAGE,
        }
        return result
