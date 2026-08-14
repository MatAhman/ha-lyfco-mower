"""Regression tests from the 2026-08-14 E1750 beta.4 field run."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
import types

ROOT = Path(__file__).parents[1] / "custom_components" / "lyfco_mower"
PKG = "lyfco_beta5_test"
package = types.ModuleType(PKG)
package.__path__ = [str(ROOT)]
sys.modules[PKG] = package

base_spec = importlib.util.spec_from_file_location(
    f"{PKG}.state_machine", ROOT / "state_machine.py"
)
assert base_spec is not None and base_spec.loader is not None
base_module = importlib.util.module_from_spec(base_spec)
sys.modules[base_spec.name] = base_module
base_spec.loader.exec_module(base_module)

final_spec = importlib.util.spec_from_file_location(
    f"{PKG}.beta5_state", ROOT / "beta5_state.py"
)
assert final_spec is not None and final_spec.loader is not None
final_module = importlib.util.module_from_spec(final_spec)
sys.modules[final_spec.name] = final_module
final_spec.loader.exec_module(final_module)

Machine = final_module.Beta5FinalStateMachine


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
    machine = Machine()
    base = datetime(2026, 8, 14, 9, 0, tzinfo=timezone.utc)
    machine.note_command("5", now_mono=0.0, now_utc=base, voltage=26.38)

    _update(machine, 26.38, 0)
    _update(machine, 26.40, 30)
    _update(machine, 26.31, 60)

    assert machine.activity == "mowing"
    assert machine.docked is False
    assert machine.charging is False


def test_reload_during_active_schedule_recovers_charging_from_sustained_rise():
    machine = Machine()
    values = [25.22, 25.25, 25.27, 25.31, 25.34, 25.36, 25.39]
    for index, voltage in enumerate(values):
        _update(machine, voltage, index * 38, active=True)

    assert machine.activity == "docked"
    assert machine.docked is True
    assert machine.charging is True
    assert machine.last_dock_evidence["source"] == "passive_startup_charge_rise"


def test_schedule_end_while_already_charging_does_not_create_returning():
    machine = Machine()
    values = [23.92, 24.64, 24.81, 24.89]
    for index, voltage in enumerate(values):
        _update(machine, voltage, index * 30, active=True, started=index == 0)

    assert machine.docked is True
    _update(machine, 24.92, 120, active=False, ended=True)

    assert machine.activity == "docked"
    assert machine.state == "docked_charging"
    assert machine.docked is True
    assert machine.last_rejected_reason == "schedule_end_while_confirmed_docked"


def test_scheduled_start_from_dock_waits_for_departure_evidence():
    machine = Machine()

    for index, voltage in enumerate([26.90, 26.94, 26.98, 27.02, 27.06, 27.10]):
        _update(machine, voltage, index * 30, active=False)
    assert machine.docked is True

    _update(machine, 27.10, 180, active=True, started=True)
    assert machine.docked is True
    assert machine.schedule_departure_pending is True

    _update(machine, 27.00, 210, active=True)
    _update(machine, 26.82, 240, active=True)

    assert machine.docked is False
    assert machine.activity == "mowing"
    assert machine.source == "schedule_departure_voltage"
