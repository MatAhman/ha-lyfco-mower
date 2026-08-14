"""Persistent observational charge-cycle tracking for Lyfco beta.5.

The learned values are diagnostics/supporting context only. They are never used
as hard dock thresholds by the beta.5 state machine.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from statistics import fmean
from typing import Any

MAX_COMPLETED_CYCLES = 20


class ChargeCycleLearning:
    """Track charging -> backoff -> reconnect cycles and retain summaries."""

    def __init__(self) -> None:
        self.phase = "unknown"
        self.last_transition_reason: str | None = None

        self.dock_detected_at_utc: datetime | None = None
        self.phase_started_at_utc: datetime | None = None
        self.phase_started_mono: float | None = None
        self.charging_confirmed_at_utc: datetime | None = None
        self.charging_confirmed_mono: float | None = None
        self.backoff_confirmed_at_utc: datetime | None = None
        self.backoff_confirmed_mono: float | None = None
        self.reconnect_confirmed_at_utc: datetime | None = None

        self.peak_voltage: float | None = None
        self.peak_voltage_at_utc: datetime | None = None
        self.bottom_voltage: float | None = None
        self.bottom_voltage_at_utc: datetime | None = None

        self.completed_cycles: deque[dict[str, Any]] = deque(
            maxlen=MAX_COMPLETED_CYCLES
        )
        self.revision = 0
        self._dirty = False

    @staticmethod
    def _iso(value: datetime | None) -> str | None:
        return None if value is None else value.isoformat()

    def observe(
        self,
        *,
        docked: bool,
        charging: bool,
        voltage: float,
        now_mono: float,
        now_utc: datetime,
        reason: str | None,
    ) -> None:
        """Observe the final beta.5 dock/charge state."""
        if not docked:
            if self.phase != "unknown":
                self._reset_live()
            return

        if self.dock_detected_at_utc is None:
            self.dock_detected_at_utc = now_utc

        if charging:
            if self.phase == "backoff":
                self._complete_cycle(now_mono, now_utc, voltage)
                self.reconnect_confirmed_at_utc = now_utc
                self._start_charging(
                    now_mono,
                    now_utc,
                    voltage,
                    "confirmed_reconnect_rise",
                )
            elif self.phase != "charging":
                self._start_charging(
                    now_mono,
                    now_utc,
                    voltage,
                    reason or "charging_confirmed",
                )
            else:
                self._update_peak(voltage, now_utc)
            return

        if self.phase == "charging":
            self.phase = "backoff"
            self.last_transition_reason = reason or "confirmed_backoff_fall"
            self.phase_started_at_utc = now_utc
            self.phase_started_mono = now_mono
            self.backoff_confirmed_at_utc = now_utc
            self.backoff_confirmed_mono = now_mono
            self.bottom_voltage = voltage
            self.bottom_voltage_at_utc = now_utc
        elif self.phase == "backoff":
            self._update_bottom(voltage, now_utc)
        else:
            self.phase = "backoff"
            self.last_transition_reason = reason or "dock_backoff_observed"
            self.phase_started_at_utc = now_utc
            self.phase_started_mono = now_mono
            self.backoff_confirmed_at_utc = now_utc
            self.backoff_confirmed_mono = now_mono
            self.bottom_voltage = voltage
            self.bottom_voltage_at_utc = now_utc

    def _start_charging(
        self,
        now_mono: float,
        now_utc: datetime,
        voltage: float,
        reason: str,
    ) -> None:
        self.phase = "charging"
        self.last_transition_reason = reason
        self.phase_started_at_utc = now_utc
        self.phase_started_mono = now_mono
        self.charging_confirmed_at_utc = now_utc
        self.charging_confirmed_mono = now_mono
        self.backoff_confirmed_at_utc = None
        self.backoff_confirmed_mono = None
        self.peak_voltage = voltage
        self.peak_voltage_at_utc = now_utc
        self.bottom_voltage = None
        self.bottom_voltage_at_utc = None

    def _update_peak(self, voltage: float, now_utc: datetime) -> None:
        if self.peak_voltage is None or voltage > self.peak_voltage:
            self.peak_voltage = voltage
            self.peak_voltage_at_utc = now_utc

    def _update_bottom(self, voltage: float, now_utc: datetime) -> None:
        if self.bottom_voltage is None or voltage < self.bottom_voltage:
            self.bottom_voltage = voltage
            self.bottom_voltage_at_utc = now_utc

    def _complete_cycle(
        self,
        now_mono: float,
        now_utc: datetime,
        reconnect_voltage: float,
    ) -> None:
        if (
            self.charging_confirmed_at_utc is None
            or self.charging_confirmed_mono is None
            or self.backoff_confirmed_at_utc is None
            or self.backoff_confirmed_mono is None
        ):
            return

        charging_duration = max(
            0.0,
            self.backoff_confirmed_mono - self.charging_confirmed_mono,
        )
        backoff_duration = max(0.0, now_mono - self.backoff_confirmed_mono)
        full_duration = max(0.0, now_mono - self.charging_confirmed_mono)
        self._update_bottom(reconnect_voltage, now_utc)

        cycle = {
            "charging_confirmed_at_utc": self.charging_confirmed_at_utc.isoformat(),
            "peak_voltage": self.peak_voltage,
            "peak_voltage_at_utc": self._iso(self.peak_voltage_at_utc),
            "backoff_confirmed_at_utc": self.backoff_confirmed_at_utc.isoformat(),
            "bottom_voltage": self.bottom_voltage,
            "bottom_voltage_at_utc": self._iso(self.bottom_voltage_at_utc),
            "reconnect_confirmed_at_utc": now_utc.isoformat(),
            "charging_duration_seconds": round(charging_duration, 1),
            "backoff_duration_seconds": round(backoff_duration, 1),
            "charge_cycle_duration_seconds": round(full_duration, 1),
        }
        self.completed_cycles.append(cycle)
        self.revision += 1
        self._dirty = True

    def _reset_live(self) -> None:
        self.phase = "unknown"
        self.last_transition_reason = None
        self.dock_detected_at_utc = None
        self.phase_started_at_utc = None
        self.phase_started_mono = None
        self.charging_confirmed_at_utc = None
        self.charging_confirmed_mono = None
        self.backoff_confirmed_at_utc = None
        self.backoff_confirmed_mono = None
        self.reconnect_confirmed_at_utc = None
        self.peak_voltage = None
        self.peak_voltage_at_utc = None
        self.bottom_voltage = None
        self.bottom_voltage_at_utc = None

    @staticmethod
    def _stats(values: list[float]) -> dict[str, float] | None:
        if not values:
            return None
        return {
            "min": round(min(values), 2),
            "avg": round(fmean(values), 2),
            "max": round(max(values), 2),
        }

    def learning_summary(self) -> dict[str, Any]:
        cycles = list(self.completed_cycles)
        peaks = [
            float(cycle["peak_voltage"])
            for cycle in cycles
            if cycle.get("peak_voltage") is not None
        ]
        bottoms = [
            float(cycle["bottom_voltage"])
            for cycle in cycles
            if cycle.get("bottom_voltage") is not None
        ]
        charging = [
            float(cycle["charging_duration_seconds"]) for cycle in cycles
        ]
        backoff = [
            float(cycle["backoff_duration_seconds"]) for cycle in cycles
        ]
        full = [
            float(cycle["charge_cycle_duration_seconds"]) for cycle in cycles
        ]
        return {
            "cycles_observed": len(cycles),
            "peak_voltage": self._stats(peaks),
            "bottom_voltage": self._stats(bottoms),
            "charging_duration_seconds": self._stats(charging),
            "backoff_duration_seconds": self._stats(backoff),
            "full_cycle_duration_seconds": self._stats(full),
        }

    def diagnostics(self, now_mono: float) -> dict[str, Any]:
        phase_duration = (
            None
            if self.phase_started_mono is None
            else round(max(0.0, now_mono - self.phase_started_mono), 1)
        )
        current_charging_duration = (
            None
            if self.phase != "charging"
            or self.charging_confirmed_mono is None
            else round(
                max(0.0, now_mono - self.charging_confirmed_mono),
                1,
            )
        )
        current_backoff_duration = (
            None
            if self.phase != "backoff"
            or self.backoff_confirmed_mono is None
            else round(
                max(0.0, now_mono - self.backoff_confirmed_mono),
                1,
            )
        )
        return {
            "phase": self.phase,
            "last_transition_reason": self.last_transition_reason,
            "references": {
                "reconnect_voltage": 28.6,
                "backoff_voltage": 29.5,
                "reference_only": True,
                "note": (
                    "Observed values are supporting context, not hard "
                    "switching thresholds."
                ),
            },
            "timing": {
                "dock_detected_at_utc": self._iso(self.dock_detected_at_utc),
                "phase_started_at_utc": self._iso(self.phase_started_at_utc),
                "current_phase_duration_seconds": phase_duration,
                "charging_confirmed_at_utc": self._iso(
                    self.charging_confirmed_at_utc
                ),
                "current_charging_duration_seconds": current_charging_duration,
                "backoff_confirmed_at_utc": self._iso(
                    self.backoff_confirmed_at_utc
                ),
                "current_backoff_duration_seconds": current_backoff_duration,
                "reconnect_confirmed_at_utc": self._iso(
                    self.reconnect_confirmed_at_utc
                ),
            },
            "extrema": {
                "peak_voltage": self.peak_voltage,
                "peak_voltage_at_utc": self._iso(self.peak_voltage_at_utc),
                "bottom_voltage": self.bottom_voltage,
                "bottom_voltage_at_utc": self._iso(self.bottom_voltage_at_utc),
            },
            "completed_cycles": list(self.completed_cycles),
            "learning_summary": self.learning_summary(),
            "learning_revision": self.revision,
        }

    def export_persistent(self) -> dict[str, Any]:
        return {
            "completed_cycles": list(self.completed_cycles),
            "learning_revision": self.revision,
        }

    def load_persistent(self, data: dict[str, Any] | None) -> None:
        if not isinstance(data, dict):
            return
        for item in data.get("completed_cycles", [])[-MAX_COMPLETED_CYCLES:]:
            if isinstance(item, dict):
                self.completed_cycles.append(item)
        self.revision = int(
            data.get("learning_revision", len(self.completed_cycles))
        )
        self._dirty = False

    def pop_dirty(self) -> bool:
        dirty = self._dirty
        self._dirty = False
        return dirty
