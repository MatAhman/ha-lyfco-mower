"""Regression tests from the 2026-08-14 E1750 beta.4 field run."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys

MODULE = (
    Path(__file__).parents[1]
    / "custom_components"
    / "lyfco_mower"
    / "state_machine.py"
)
SPEC = importlib.util.spec_from_file_location("lyfco_beta5_state_machine", MODULE)
assert SPEC is not None and SPEC.loader is not None
sm = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sm
SPEC.loader.exec_module(sm)


def _update(machine, voltage, seconds, *, active=True, started=False, ended=False):
    base = datetime(2026, 8, 14, 9, 0, tzinfo=timezone.utc)
    machine.update(
        voltage=voltage,
        alarm_flags=(False,) * 14,
        raw_docked=voltage >= 26.4,
        raw_charging=voltage >= 26.4,
        schedule_active=active,
        schedule_started=started,
        schedule_ended=ended,
        now_mono=float(seconds),
        now_utc=base + timedelta(seconds=seconds),
    )


def test_264_crossing_while_mowing_does_not_dock():
    machine = sm.Beta5StateMachine()
    base = datetime(2026, 8, 14, 9, 0, tzinfo=timezone.utc)
    machine.note_command("5", now_mono=0.0, now_utc=base, voltage=26.38)

    _update(machine, 26.38, 0)
    _update(machine, 26.40, 30)
    _update(machine, 26.31, 60)

    assert machine.activity == "mowing"
    assert machine.docked is False
    assert machine.charging is False


def test_reload_during_active_schedule_recovers_charging_from_sustained_rise():
    machine = sm.Beta5StateMachine()
    values = [25.22, 25.25, 25.27, 25.31, 25.34, 25.36, 25.39]
    for index, voltage in enumerate(values):
        _update(machine, voltage, index * 38, active=True)

    assert machine.activity == "docked"
    assert machine.docked is True
    assert machine.charging is True
    assert machine.last_dock_evidence["source"] == "passive_startup_charge_rise"


def test_schedule_end_while_already_charging_does_not_create_returning():
    machine = sm.Beta5StateMachine()
    values = [23.92, 24.64, 24.81, 24.89]
    for index, voltage in enumerate(values):
        _update(machine, voltage, index * 30, active=True, started=index == 0)

    assert machine.docked is True
    _update(machine, 24.92, 120, active=False, ended=True)

    assert machine.activity == "docked"
    assert machine.state == "docked_charging"
    assert machine.docked is True
    assert machine.last_rejected_reason == "schedule_end_while_confirmed_docked"
