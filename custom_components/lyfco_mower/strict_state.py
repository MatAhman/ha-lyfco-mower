"""Strict final-state transition layer for Lyfco beta.5."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .state_machine import (
    Beta5StateMachine,
    _VoltageSample,
)


class StrictBeta5StateMachine(Beta5StateMachine):
    """Use sustained evidence for charging/backoff/reconnect phase changes."""

    def __init__(self) -> None:
        super().__init__()
        self._strict_charge_state_started_mono: float | None = None
        self.last_charge_phase_evidence: dict[str, Any] | None = None

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
        """Consume one real W sample with guarded dock charge phases."""
        sample = _VoltageSample(now_mono, now_utc, voltage)
        self._samples.append(sample)
        changed = False

        if (
            not self.docked
            and self.activity == "mowing"
            and len(alarm_flags) >= 3
            and alarm_flags[2]
        ):
            self.return_context_active = True
            self.return_context_reason = "low_battery_alarm"
            self.return_started_mono = now_mono
            changed |= self._set_state(
                "returning",
                "returning",
                False,
                False,
                "low_battery_alarm",
                now_mono,
                now_utc,
                voltage,
            )

        dock_evidence = self._confirmed_dock_evidence(now_mono)
        if not self.docked and dock_evidence is not None:
            self.last_dock_evidence = dock_evidence
            if dock_evidence["kind"].startswith("passive"):
                self.last_passive_evidence = dock_evidence
            self.return_context_active = False
            self.return_context_reason = None
            self.return_started_mono = None
            self._explicit_hold = False
            changed |= self._set_state(
                "docked_charging",
                "docked",
                True,
                True,
                dock_evidence["source"],
                now_mono,
                now_utc,
                voltage,
            )

        if raw_docked and not self.docked:
            if not self._raw_dock_rejection_active:
                self._reject(
                    "raw_dock_without_confirmed_trend", voltage, now_utc
                )
                self._raw_dock_rejection_active = True
        else:
            self._raw_dock_rejection_active = False

        if self.docked:
            if schedule_started:
                self._reject(
                    "schedule_start_while_confirmed_docked", voltage, now_utc
                )
            if schedule_ended:
                self._reject(
                    "schedule_end_while_confirmed_docked", voltage, now_utc
                )

            charging = self.charging
            source = self.source
            phase_started = self._strict_charge_state_started_mono
            phase_samples = [
                item
                for item in self._samples
                if phase_started is None or item.at_mono >= phase_started
            ]

            if charging:
                evidence = self._fall_trend_evidence(
                    phase_samples,
                    min_samples=4,
                    min_fall=0.10,
                    max_counter=0.04,
                )
                if evidence is not None:
                    evidence.update(
                        {
                            "direction": "falling",
                            "source": "confirmed_backoff_fall",
                        }
                    )
                    self.last_charge_phase_evidence = evidence
                    charging = False
                    source = "confirmed_backoff_fall"
            else:
                evidence = self._trend_evidence(
                    phase_samples,
                    min_samples=4,
                    min_seconds=0.0,
                    min_rise=0.10,
                    max_counter=0.04,
                )
                if evidence is not None:
                    evidence.update(
                        {
                            "direction": "rising",
                            "source": "confirmed_reconnect_rise",
                        }
                    )
                    self.last_charge_phase_evidence = evidence
                    charging = True
                    source = "confirmed_reconnect_rise"

            desired_state = "docked_charging" if charging else "docked_backoff"
            changed |= self._set_state(
                desired_state,
                "docked",
                True,
                charging,
                source or "remembered_dock",
                now_mono,
                now_utc,
                voltage,
            )
        else:
            if schedule_started and not self._explicit_hold:
                self.return_context_active = False
                self.return_context_reason = None
                self.return_started_mono = None
                changed |= self._set_state(
                    "mowing",
                    "mowing",
                    False,
                    False,
                    "schedule_clock_start",
                    now_mono,
                    now_utc,
                    voltage,
                )
            elif schedule_ended:
                if self.activity != "paused":
                    self.return_context_active = True
                    self.return_context_reason = "schedule_end"
                    self.return_started_mono = now_mono
                    changed |= self._set_state(
                        "returning",
                        "returning",
                        False,
                        False,
                        "schedule_clock_end",
                        now_mono,
                        now_utc,
                        voltage,
                    )
            elif schedule_active and not self._explicit_hold:
                if self.activity in {None, "paused"} and self._last_command != "0":
                    changed |= self._set_state(
                        "mowing",
                        "mowing",
                        False,
                        False,
                        "schedule_clock_active",
                        now_mono,
                        now_utc,
                        voltage,
                    )
                elif self.activity == "mowing" and self.source not in {
                    "last_command",
                    "low_battery_alarm",
                }:
                    self.source = "schedule_clock_active"

        self._record_voltage(now_utc, voltage)
        return changed

    @staticmethod
    def _fall_trend_evidence(
        samples: list[_VoltageSample],
        *,
        min_samples: int,
        min_fall: float,
        max_counter: float,
    ) -> dict[str, Any] | None:
        if len(samples) < min_samples:
            return None

        for start_index in range(0, len(samples) - min_samples + 1):
            window = samples[start_index:]
            if len(window) < min_samples:
                continue
            total = window[0].voltage - window[-1].voltage
            if total < min_fall:
                continue
            counter = 0.0
            for left, right in zip(window, window[1:]):
                change = right.voltage - left.voltage
                if change > 0:
                    counter += change
            if counter > max_counter:
                continue
            return {
                "samples": len(window),
                "duration_seconds": round(
                    window[-1].at_mono - window[0].at_mono, 1
                ),
                "total_change": round(total, 3),
                "counter_movement": round(counter, 3),
                "start_voltage": window[0].voltage,
                "end_voltage": window[-1].voltage,
                "start_at_utc": window[0].at_utc.isoformat(),
                "end_at_utc": window[-1].at_utc.isoformat(),
            }
        return None

    def _set_state(
        self,
        state: str,
        activity: str | None,
        docked: bool,
        charging: bool,
        source: str | None,
        now_mono: float,
        now_utc: datetime,
        voltage: float | None,
    ) -> bool:
        if self.docked != docked or self.charging != charging:
            self._strict_charge_state_started_mono = now_mono
        return super()._set_state(
            state,
            activity,
            docked,
            charging,
            source,
            now_mono,
            now_utc,
            voltage,
        )

    def diagnostics(self, now_mono: float) -> dict[str, Any]:
        result = super().diagnostics(now_mono)
        result["dock_evidence"]["last_charge_phase"] = (
            self.last_charge_phase_evidence
        )
        result["dock_evidence"]["parameters"].update(
            {
                "charge_phase_min_samples": 4,
                "charge_phase_min_total_change": 0.10,
                "charge_phase_max_counter_movement": 0.04,
            }
        )
        return result
