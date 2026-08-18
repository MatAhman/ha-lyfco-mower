"""Regression tests for old and new EGROBOT W alarm layouts."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

ROOT = Path(__file__).parents[1] / "custom_components" / "lyfco_mower"
PKG = "egrobot_protocol_test"
package = types.ModuleType(PKG)
package.__path__ = [str(ROOT)]
sys.modules[PKG] = package

const_spec = importlib.util.spec_from_file_location(f"{PKG}.const", ROOT / "const.py")
assert const_spec is not None and const_spec.loader is not None
const_module = importlib.util.module_from_spec(const_spec)
sys.modules[const_spec.name] = const_module
const_spec.loader.exec_module(const_module)

protocol_spec = importlib.util.spec_from_file_location(
    f"{PKG}.protocol", ROOT / "protocol.py"
)
assert protocol_spec is not None and protocol_spec.loader is not None
protocol_module = importlib.util.module_from_spec(protocol_spec)
sys.modules[protocol_spec.name] = protocol_module
protocol_spec.loader.exec_module(protocol_module)

build_command = protocol_module.build_command
parse_status = protocol_module.parse_status


def _status_body(alarms: str) -> str:
    # runtime=7 h, charging=14 h, voltage=28.73 V
    return f"00000700142873{alarms}"


def test_old_11_alarm_layout_is_accepted_and_padded():
    status = parse_status(build_command("W", _status_body("00000000001")))
    assert len(status.alarm_flags) == 14
    assert status.alarm_flags[10] is True
    assert status.alarm_flags[11:] == (False, False, False)
    assert status.active_alarm_keys == ("mower_tilted",)


def test_new_14_alarm_layout_maps_final_three_alarms():
    status = parse_status(build_command("W", _status_body("00000000000111")))
    assert len(status.alarm_flags) == 14
    assert status.alarm_flags[11:] == (True, True, True)
    assert status.active_alarm_keys == (
        "wire_signal_lost",
        "outside_boundary",
        "mower_stuck",
    )
