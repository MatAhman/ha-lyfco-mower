"""Beta.5 state inference for Lyfco/Miotlink robot mowers.

Absolute voltage is supporting context only. Confirmed commands, schedule context
and sustained voltage trends are combined into one final state consumed by all
Home Assistant entities.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

VOLTAGE_EPSILON = 0.02

RETURN_SETTLE_SECONDS = 20.0
RETURN_MIN_SAMPLES = 4
RETURN_MIN_RISE = 0.10
RETURN_MAX_COUNTER_MOVEMENT = 0.04

PASSIVE_STARTUP_MIN_SAMPLES = 6
PASSIVE_STARTUP_MIN_SECONDS = 120.0
PASSIVE_STARTUP_MIN_RISE = 0.15

PASSIVE_ACTIVE_MIN_SAMPLES = 8
PASSIVE_ACTIVE_MIN_SECONDS = 180.0
PASSIVE_ACTIVE_MIN_RISE = 0.20
PASSIVE_MAX_COUNTER_MOVEMENT = 0.04
PASSIVE_WINDOW_SECONDS = 420.0

DOCK_JUMP_MIN = 0.30
DOCK_JUMP_FOLLOW_SAMPLES = 3
DOCK_JUMP_FOLLOW_RISE = 0.10

CHARGE_BACKOFF_REFERENCE = 29.5
CHARGE_RECONNECT_REFERENCE = 28.6

VOLTAGE_HISTORY_SIZE = 2000
TRANSITION_HISTORY_SIZE = 100
MOWING_HISTORY_SIZE = 20
CHARGE_HISTORY_SIZE = 20


@dataclass(slots=True)
class _VoltageSample:
    at_mono: float
    at_utc: datetime
    voltage: float


class Beta5StateMachine:
    """Infer mower state without allowing schedule or voltage to dominate alone."""

    def __init__(self) -> None:
        self.state = "unknown"
        self.activity: str | None = None
        self.docked = False
        self.charging = False
        self.source: str | None = None

        self.return_context_active = False
        self.return_context_reason: str | None = None
        self.return_started_mono: float | None = None

        self._samples: deque[_VoltageSample] = deque(maxlen=VOLTAGE_HISTORY_SIZE)
        self._voltage_history: deque[dict[str, Any]] = deque(
            maxlen=VOLTAGE_HISTORY_SIZE
        )
        self._transitions: deque[dict[str, Any]] = deque(
            maxlen=TRANSITION_HISTORY_SIZE
        )

        self._raw_dock_rejection_active = False
        self.contradictions_rejected = 0
        self.last_rejected_reason: str | None = None
        self.last_rejected_voltage: float | None = None
        self.last_rejected_at_utc: str | None = None

        self._mowing_started_at: datetime | None = None
        self._mowing_started_mono: float | None = None
        self._mowing_sessions: deque[dict[str, Any]] = deque(
            maxlen=MOWING_HISTORY_SIZE
        )

        self._charging_started_at: datetime | None = None
        self._charging_started_mono: float | None = None
        self._charge_phases: deque[dict[str, Any]] = deque(
            maxlen=CHARGE_HISTORY_SIZE
        )
        self._persistent_dirty = False

        self.last_dock_evidence: dict[str, Any] | None = None
        self.last_passive_evidence: dict[str, Any] | None = None

        self._explicit_hold = False
        self._last_command: str | None = None

    @staticmethod
    def _iso(value: datetime | None) -> str | None:
        return None if value is None else value.isoformat()

    def note_command(
        self,
        action: str | None,
        *,
        now_mono: float,
        now_utc: datetime,
        voltage: float | None = None,
    ) -> bool:
        """Apply one verified Y command before the next status sample."""
        if action is None:
            return False
        self._last_command = action

        if action == "0":
            self._explicit_hold = True
            self.return_context_active = False
            return self._set_state(
                "paused", "paused", False, False, "last_command",
                now_mono, now_utc, voltage
            )

        if action == "5":
            self._explicit_hold = False
            self.return_context_active = False
            return self._set_state(
                "mowing", "mowing", False, False, "last_command",
                now_mono, now_utc, voltage
            )

        if action == "7":
            self._explicit_hold = False
            self.return_context_active = True
            self.return_context_reason = "home_command"
            self.return_started_mono = now_mono
            return self._set_state(
                "returning", "returning", False, False, "last_command",
                now_mono, now_utc, voltage
            )

        if action == "6":
            self._explicit_hold = True
            self.return_context_active = False
            return self._set_state(
                "paused", "paused", False, False, "last_command",
                now_mono, now_utc, voltage
            )

        if action in {"1", "2", "3", "4"}:
            self._explicit_hold = False
            self.return_context_active = False
            return self._set_state(
                "mowing", "mowing", False, False, "last_command",
                now_mono, now_utc, voltage
            )

        return False

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
        """Consume one real W sample and return whether final state changed."""
        sample = _VoltageSample(now_mono, now_utc, voltage)
        previous = self._samples[-1] if self._samples else None
        self._samples.append(sample)
        delta = None if previous is None else voltage - previous.voltage

        changed = False

        # A real low-battery alarm during mowing is valid return evidence.
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
                "returning", "returning", False, False, "low_battery_alarm",
                now_mono, now_utc, voltage
            )

        # Physical dock evidence is evaluated before schedule boundaries. A
        # mower already charging must not become Returning at schedule end.
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
                "docked_charging", "docked", True, True,
                dock_evidence["source"], now_mono, now_utc, voltage
            )

        # Never accept the legacy raw 26.4-V dock candidate without our own
        # trend evidence. Count one contradiction per raw-dock streak.
        if raw_docked and not self.docked:
            if not self._raw_dock_rejection_active:
                self._reject("raw_dock_without_confirmed_trend", voltage, now_utc)
                self._raw_dock_rejection_active = True
        else:
            self._raw_dock_rejection_active = False

        if self.docked:
            if schedule_started:
                self._reject("schedule_start_while_confirmed_docked", voltage, now_utc)
            if schedule_ended:
                self._reject("schedule_end_while_confirmed_docked", voltage, now_utc)

            charging = self.charging
            source = self.source

            if delta is not None:
                if delta >= VOLTAGE_EPSILON:
                    charging = True
                    source = "charge_cycle_charging"
                elif delta <= -VOLTAGE_EPSILON:
                    charging = False
                    source = "charge_cycle_backoff"

            # These are reference zones only. They may refine charging/backoff
            # after docking has been proven, but can never prove docking.
            if (
                voltage >= CHARGE_BACKOFF_REFERENCE
                and delta is not None
                and delta <= 0
            ):
                charging = False
                source = "charge_cycle_backoff"
            elif (
                voltage <= CHARGE_RECONNECT_REFERENCE
                and delta is not None
                and delta > 0
            ):
                charging = True
                source = "charge_cycle_charging"

            desired_state = "docked_charging" if charging else "docked_backoff"
            changed |= self._set_state(
                desired_state, "docked", True, charging,
                source or "remembered_dock", now_mono, now_utc, voltage
            )
        else:
            # Schedule is an expectation only when no stronger physical or
            # explicit-command state exists.
            if schedule_started and not self._explicit_hold:
                self.return_context_active = False
                self.return_context_reason = None
                self.return_started_mono = None
                changed |= self._set_state(
                    "mowing", "mowing", False, False, "schedule_clock_start",
                    now_mono, now_utc, voltage
                )
            elif schedule_ended:
                if self.activity != "paused":
                    self.return_context_active = True
                    self.return_context_reason = "schedule_end"
                    self.return_started_mono = now_mono
                    changed |= self._set_state(
                        "returning", "returning", False, False,
                        "schedule_clock_end", now_mono, now_utc, voltage
                    )
            elif schedule_active and not self._explicit_hold:
                if self.activity in {None, "paused"} and self._last_command != "0":
                    changed |= self._set_state(
                        "mowing", "mowing", False, False,
                        "schedule_clock_active", now_mono, now_utc, voltage
                    )
                elif self.activity == "mowing" and self.source not in {
                    "last_command", "low_battery_alarm"
                }:
                    self.source = "schedule_clock_active"

        self._record_voltage(now_utc, voltage)
        return changed

    def _confirmed_dock_evidence(self, now_mono: float) -> dict[str, Any] | None:
        """Return confirmed return/passive charging evidence, if any."""
        if len(self._samples) < 2:
            return None

        if self.return_context_active and self.return_started_mono is not None:
            if now_mono - self.return_started_mono >= RETURN_SETTLE_SECONDS:
                samples = [
                    sample for sample in self._samples
                    if sample.at_mono >= self.return_started_mono
                ]
                evidence = self._trend_evidence(
                    samples,
                    min_samples=RETURN_MIN_SAMPLES,
                    min_seconds=0.0,
                    min_rise=RETURN_MIN_RISE,
                    max_counter=RETURN_MAX_COUNTER_MOVEMENT,
                )
                if evidence is not None:
                    evidence.update({
                        "kind": "return_rise",
                        "source": "confirmed_return_charge_rise_after_settle",
                    })
                    return evidence

        recent = [
            sample for sample in self._samples
            if now_mono - sample.at_mono <= PASSIVE_WINDOW_SECONDS
        ]

        jump = self._dock_jump_evidence(recent)
        if jump is not None:
            jump.update({
                "kind": "passive_jump_and_rise",
                "source": "passive_charge_jump_and_rise",
            })
            return jump

        # After reload/startup use a shorter passive trend, but still require
        # enough samples and time to make a one-poll 26.4-V event impossible.
        startup_like = self.state == "unknown" or (
            len(self._voltage_history) < PASSIVE_ACTIVE_MIN_SAMPLES
        )
        if startup_like:
            evidence = self._trend_evidence(
                recent,
                min_samples=PASSIVE_STARTUP_MIN_SAMPLES,
                min_seconds=PASSIVE_STARTUP_MIN_SECONDS,
                min_rise=PASSIVE_STARTUP_MIN_RISE,
                max_counter=PASSIVE_MAX_COUNTER_MOVEMENT,
            )
            if evidence is not None:
                evidence.update({
                    "kind": "passive_startup_rise",
                    "source": "passive_startup_charge_rise",
                })
                return evidence

        # During an active mowing assumption require a longer sustained rise.
        evidence = self._trend_evidence(
            recent,
            min_samples=PASSIVE_ACTIVE_MIN_SAMPLES,
            min_seconds=PASSIVE_ACTIVE_MIN_SECONDS,
            min_rise=PASSIVE_ACTIVE_MIN_RISE,
            max_counter=PASSIVE_MAX_COUNTER_MOVEMENT,
        )
        if evidence is not None:
            evidence.update({
                "kind": "passive_sustained_rise",
                "source": "passive_charge_rise",
            })
            return evidence

        return None

    @staticmethod
    def _trend_evidence(
        samples: list[_VoltageSample],
        *,
        min_samples: int,
        min_seconds: float,
        min_rise: float,
        max_counter: float,
    ) -> dict[str, Any] | None:
        if len(samples) < min_samples:
            return None

        # Search suffixes so older unrelated movement does not poison a newly
        # started charging trend.
        for start_index in range(0, len(samples) - min_samples + 1):
            window = samples[start_index:]
            if len(window) < min_samples:
                continue
            duration = window[-1].at_mono - window[0].at_mono
            if duration < min_seconds:
                continue
            total = window[-1].voltage - window[0].voltage
            if total < min_rise:
                continue
            counter = 0.0
            for left, right in zip(window, window[1:]):
                change = right.voltage - left.voltage
                if change < 0:
                    counter += -change
            if counter > max_counter:
                continue
            return {
                "samples": len(window),
                "duration_seconds": round(duration, 1),
                "total_change": round(total, 3),
                "counter_movement": round(counter, 3),
                "start_voltage": window[0].voltage,
                "end_voltage": window[-1].voltage,
                "start_at_utc": window[0].at_utc.isoformat(),
                "end_at_utc": window[-1].at_utc.isoformat(),
            }
        return None

    @staticmethod
    def _dock_jump_evidence(samples: list[_VoltageSample]) -> dict[str, Any] | None:
        if len(samples) < DOCK_JUMP_FOLLOW_SAMPLES + 1:
            return None
        for index in range(len(samples) - DOCK_JUMP_FOLLOW_SAMPLES):
            jump = samples[index + 1].voltage - samples[index].voltage
            if jump < DOCK_JUMP_MIN:
                continue
            follow = samples[index + 1:index + 1 + DOCK_JUMP_FOLLOW_SAMPLES]
            if len(follow) < DOCK_JUMP_FOLLOW_SAMPLES:
                continue
            counter = 0.0
            for left, right in zip(follow, follow[1:]):
                change = right.voltage - left.voltage
                if change < 0:
                    counter += -change
            follow_rise = follow[-1].voltage - follow[0].voltage
            if (
                counter <= PASSIVE_MAX_COUNTER_MOVEMENT
                and follow_rise >= DOCK_JUMP_FOLLOW_RISE
            ):
                return {
                    "samples": len(follow) + 1,
                    "duration_seconds": round(
                        follow[-1].at_mono - samples[index].at_mono, 1
                    ),
                    "jump": round(jump, 3),
                    "total_change": round(
                        follow[-1].voltage - samples[index].voltage, 3
                    ),
                    "counter_movement": round(counter, 3),
                    "start_voltage": samples[index].voltage,
                    "end_voltage": follow[-1].voltage,
                    "start_at_utc": samples[index].at_utc.isoformat(),
                    "end_at_utc": follow[-1].at_utc.isoformat(),
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
        old_state = self.state
        old_activity = self.activity
        old_docked = self.docked
        old_charging = self.charging

        if old_activity == "mowing" and activity != "mowing":
            self._close_mowing_session(now_mono, now_utc, state)
        elif old_activity != "mowing" and activity == "mowing":
            self._mowing_started_at = now_utc
            self._mowing_started_mono = now_mono

        if old_charging and not charging:
            self._close_charging_phase(now_mono, now_utc)
        elif not old_charging and charging:
            self._charging_started_at = now_utc
            self._charging_started_mono = now_mono

        self.state = state
        self.activity = activity
        self.docked = docked
        self.charging = charging
        self.source = source

        changed = (
            old_state != state
            or old_activity != activity
            or old_docked != docked
            or old_charging != charging
        )
        if changed:
            self._transitions.append({
                "at_utc": now_utc.isoformat(),
                "from_state": old_state,
                "to_state": state,
                "voltage": voltage,
                "reason": source,
                "command": self._last_command,
            })
        return changed

    def _close_mowing_session(
        self, now_mono: float, now_utc: datetime, end_reason: str
    ) -> None:
        if self._mowing_started_at is None or self._mowing_started_mono is None:
            return
        duration = max(0.0, now_mono - self._mowing_started_mono)
        self._mowing_sessions.append({
            "started_at_utc": self._mowing_started_at.isoformat(),
            "ended_at_utc": now_utc.isoformat(),
            "duration_seconds": round(duration, 1),
            "duration_minutes": round(duration / 60.0, 1),
            "end_reason": end_reason,
        })
        self._mowing_started_at = None
        self._mowing_started_mono = None
        self._persistent_dirty = True

    def _close_charging_phase(self, now_mono: float, now_utc: datetime) -> None:
        if self._charging_started_at is None or self._charging_started_mono is None:
            return
        duration = max(0.0, now_mono - self._charging_started_mono)
        self._charge_phases.append({
            "started_at_utc": self._charging_started_at.isoformat(),
            "ended_at_utc": now_utc.isoformat(),
            "duration_seconds": round(duration, 1),
            "duration_minutes": round(duration / 60.0, 1),
        })
        self._charging_started_at = None
        self._charging_started_mono = None
        self._persistent_dirty = True

    def _reject(self, reason: str, voltage: float, now_utc: datetime) -> None:
        self.contradictions_rejected += 1
        self.last_rejected_reason = reason
        self.last_rejected_voltage = voltage
        self.last_rejected_at_utc = now_utc.isoformat()

    def _record_voltage(self, now_utc: datetime, voltage: float) -> None:
        self._voltage_history.append({
            "at_utc": now_utc.isoformat(),
            "voltage": voltage,
            "state": self.state,
            "activity": self.activity,
            "docked": self.docked,
            "charging": self.charging,
            "source": self.source,
            "command": self._last_command,
        })

    def current_charging_minutes(self, now_mono: float) -> float:
        if not self.charging or self._charging_started_mono is None:
            return 0.0
        return round(max(0.0, now_mono - self._charging_started_mono) / 60.0, 1)

    def current_mowing_minutes(self, now_mono: float) -> float:
        if self.activity != "mowing" or self._mowing_started_mono is None:
            return 0.0
        return round(max(0.0, now_mono - self._mowing_started_mono) / 60.0, 1)

    def export_persistent(self) -> dict[str, Any]:
        return {
            "format": 1,
            "completed_charge_phases": list(self._charge_phases),
            "mowing_sessions": list(self._mowing_sessions),
        }

    def load_persistent(self, data: dict[str, Any] | None) -> None:
        if not data or data.get("format") != 1:
            return
        for item in data.get("completed_charge_phases", [])[-CHARGE_HISTORY_SIZE:]:
            if isinstance(item, dict):
                self._charge_phases.append(item)
        for item in data.get("mowing_sessions", [])[-MOWING_HISTORY_SIZE:]:
            if isinstance(item, dict):
                self._mowing_sessions.append(item)
        self._persistent_dirty = False

    def pop_persistent_dirty(self) -> bool:
        dirty = self._persistent_dirty
        self._persistent_dirty = False
        return dirty

    def diagnostics(self, now_mono: float) -> dict[str, Any]:
        samples = list(self._samples)
        return {
            "state": self.state,
            "activity": self.activity,
            "docked_latched": self.docked,
            "charging_latched": self.charging,
            "inference_source": self.source,
            "last_command": self._last_command,
            "last_voltage": samples[-1].voltage if samples else None,
            "return_context_active": self.return_context_active,
            "return_context_reason": self.return_context_reason,
            "current_charging_minutes": self.current_charging_minutes(now_mono),
            "current_mowing_minutes": self.current_mowing_minutes(now_mono),
            "state_consistency": {
                "contradictions_rejected": self.contradictions_rejected,
                "last_rejected_reason": self.last_rejected_reason,
                "last_rejected_voltage": self.last_rejected_voltage,
                "last_rejected_at_utc": self.last_rejected_at_utc,
            },
            "dock_evidence": {
                "last_confirmed": self.last_dock_evidence,
                "last_passive": self.last_passive_evidence,
                "parameters": {
                    "absolute_voltage_alone_can_prove_docked": False,
                    "return_settle_seconds": RETURN_SETTLE_SECONDS,
                    "return_min_samples": RETURN_MIN_SAMPLES,
                    "return_min_rise": RETURN_MIN_RISE,
                    "passive_startup_min_samples": PASSIVE_STARTUP_MIN_SAMPLES,
                    "passive_startup_min_seconds": PASSIVE_STARTUP_MIN_SECONDS,
                    "passive_startup_min_rise": PASSIVE_STARTUP_MIN_RISE,
                    "passive_active_min_samples": PASSIVE_ACTIVE_MIN_SAMPLES,
                    "passive_active_min_seconds": PASSIVE_ACTIVE_MIN_SECONDS,
                    "passive_active_min_rise": PASSIVE_ACTIVE_MIN_RISE,
                    "dock_jump_min": DOCK_JUMP_MIN,
                },
            },
            "recent_transitions": list(self._transitions),
            "mowing_history": {
                "current_started_at_utc": self._iso(self._mowing_started_at),
                "latest": self._mowing_sessions[-1] if self._mowing_sessions else None,
                "previous": (
                    self._mowing_sessions[-2]
                    if len(self._mowing_sessions) >= 2 else None
                ),
                "recent": list(self._mowing_sessions),
            },
            "charge_history": {
                "current_started_at_utc": self._iso(self._charging_started_at),
                "completed_phases": list(self._charge_phases),
            },
            "voltage_observation_history": {
                "capacity": VOLTAGE_HISTORY_SIZE,
                "count": len(self._voltage_history),
                "samples": list(self._voltage_history),
            },
        }
