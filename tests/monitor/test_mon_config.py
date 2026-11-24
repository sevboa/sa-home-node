import pytest
from pydantic import ValidationError

from sa_home_node.monitor.config import (
    MonitorConfig,
    ScheduleConfig,
    SensorsConfig,
    apply_node_kind,
)


@pytest.mark.parametrize(
    ("kind", "raw", "cpu", "disks", "host"),
    [
        ("workstation", {}, True, True, False),
        ("server", {}, True, True, False),
        ("vps", {}, False, False, True),
        ("vps", {"cpu": {"enabled": True}}, True, False, True),
        ("vps", {"disks": {"enabled": True}, "host": {"enabled": False}}, False, True, False),
        ("workstation", {"host": {"enabled": True}}, True, True, True),
        ("server", {"cpu": {"enabled": False}}, False, True, False),
    ],
)
def test_kind_defaults(kind, raw, cpu, disks, host):
    result = apply_node_kind(SensorsConfig.model_validate(raw), kind)
    assert (result.cpu.enabled, result.disks.enabled, result.host.enabled) == (cpu, disks, host)


def test_apply_does_not_mutate_source():
    sensors = SensorsConfig()
    apply_node_kind(sensors, "vps")
    assert sensors.host.enabled is None
    assert sensors.cpu.enabled is True


def test_defaults():
    sensors = SensorsConfig()
    assert (sensors.cpu.warn_c, sensors.cpu.crit_c, sensors.cpu.consecutive_to_alert) == (80.0, 90.0, 3)
    assert (sensors.disks.warn_c, sensors.disks.crit_c, sensors.disks.consecutive_to_clear) == (55.0, 65.0, 2)
    assert sensors.gpu.enabled is False
    assert sensors.host.sample_window_s == 10.0
    assert ScheduleConfig().model_dump() == {
        "scan_cron": "*/1 * * * *",
        "smart_cron": "0 * * * *",
        "housekeeping_cron": "0 3 * * *",
    }
    monitor = MonitorConfig()
    assert monitor.socket == "./data/monitor.sock"
    assert str(monitor.db_path) == "data/monitor.sqlite"


def test_unknown_lhm_section_allowed():
    sensors = SensorsConfig.model_validate({"lhm": {"dll_path": "x.dll"}})
    assert sensors.model_extra == {"lhm": {"dll_path": "x.dll"}}


def test_host_thresholds_parse():
    sensors = SensorsConfig.model_validate({"host": {"thresholds": {"steal": {"warn": 5, "crit": 10}}}})
    assert sensors.host.thresholds["steal"].crit == 10.0


@pytest.mark.parametrize(
    "raw",
    [
        {"cpu": {"consecutive_to_alert": 0}},
        {"disks": {"consecutive_to_clear": 0}},
        {"gpu": {"baseline_window": 0}},
        {"cpu": {"baseline_k_sigma": 0}},
        {"cpu": {"baseline_min_std_c": -1}},
        {"host": {"sample_window_s": 0}},
        {"host": {"consecutive_to_alert": 0}},
        {"cpu": {"mode": "other"}},
    ],
)
def test_bounds_rejected(raw):
    with pytest.raises(ValidationError):
        SensorsConfig.model_validate(raw)
