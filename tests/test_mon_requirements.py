import pytest

from sa_home_node import host
from sa_home_node.requirements import (
    Requirement,
    RequirementRegistry,
    RequirementStatus,
    looks_like_permission_error,
    requirements_registry,
)

SMARTCTL = Requirement(program="smartctl", package="smartmontools", note="температура дисков")


class FakeHost:
    def __init__(self, name):
        self.name = name


def set_env(monkeypatch, programs=(), platform="linux"):
    monkeypatch.setattr("shutil.which", lambda n: f"/usr/bin/{n}" if n in programs else None)
    monkeypatch.setattr(host, "current", lambda: FakeHost(platform))


@pytest.mark.parametrize(
    ("programs", "platforms", "platform", "status"),
    [
        (("smartctl",), None, "linux", RequirementStatus.OK),
        ((), None, "linux", RequirementStatus.MISSING_PROGRAM),
        (("smartctl",), ("windows",), "linux", RequirementStatus.UNSUPPORTED_PLATFORM),
        ((), ("windows",), "linux", RequirementStatus.UNSUPPORTED_PLATFORM),
        (("smartctl",), ("linux", "windows"), "windows", RequirementStatus.OK),
    ],
)
def test_diagnose(monkeypatch, programs, platforms, platform, status):
    set_env(monkeypatch, programs, platform)
    req = Requirement(program="smartctl", package="smartmontools", platforms=platforms)
    assert req.diagnose() is status
    assert req.available() is (status is RequirementStatus.OK)


@pytest.mark.parametrize(
    ("manager", "expected"),
    [
        ("apt", "sudo apt install smartmontools"),
        ("pacman", "sudo pacman -S smartmontools"),
        ("winget", "winget install smartmontools"),
        (None, "установите пакет 'smartmontools' через пакетный менеджер системы"),
    ],
)
def test_install_hint(monkeypatch, manager, expected):
    set_env(monkeypatch, (manager,) if manager else ())
    assert SMARTCTL.install_hint() == f"{expected} (нужна команда 'smartctl') — температура дисков"


def test_install_hint_unsupported_platform(monkeypatch):
    set_env(monkeypatch)
    req = Requirement(platforms=("windows", "darwin"), note="служба")
    assert req.install_hint() == (
        "не поддерживается на этой ОС (пока есть только для windows / darwin) — служба"
    )


def test_privilege_hint():
    assert SMARTCTL.privilege_hint() == (
        "не хватает прав на команду 'smartctl' — выполните на ноде `nodectl fix`"
        " — температура дисков"
    )


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("Permission denied", True),
        ("smartctl: Operation not permitted", True),
        ("You must be root to run this program", True),
        ("PATH ERROR: Access is denied.", True),
        ("", False),
        ("smartctl: command not found", False),
        ("timeout", False),
    ],
)
def test_looks_like_permission_error(stderr, expected):
    assert looks_like_permission_error(stderr) is expected


def test_problem_for_table(monkeypatch):
    registry = RequirementRegistry()
    set_env(monkeypatch, ("smartctl",))
    assert registry.problem_for(SMARTCTL) is None
    registry.report(SMARTCTL, RequirementStatus.NEEDS_PRIVILEGE)
    assert registry.problem_for(SMARTCTL) == {
        "id": "smartctl",
        "status": "needs_privilege",
        "hint": SMARTCTL.privilege_hint(),
    }
    set_env(monkeypatch, ())
    problem = registry.problem_for(SMARTCTL)
    assert problem["status"] == "missing_program"
    assert problem["hint"] == SMARTCTL.install_hint()


def test_status_for_prefers_static_and_reset(monkeypatch):
    registry = RequirementRegistry()
    set_env(monkeypatch, ("smartctl",))
    registry.report(SMARTCTL, RequirementStatus.NEEDS_PRIVILEGE)
    set_env(monkeypatch, ())
    assert registry.status_for(SMARTCTL) is RequirementStatus.MISSING_PROGRAM
    set_env(monkeypatch, ("smartctl",))
    registry.reset()
    assert registry.status_for(SMARTCTL) is RequirementStatus.OK


def test_key_and_id_fallbacks(monkeypatch):
    set_env(monkeypatch)
    registry = RequirementRegistry()
    keyed = Requirement(program="x", key="k")
    noted = Requirement(platforms=("windows",), note="n")
    assert registry.problem_for(keyed)["id"] == "k"
    assert registry.problem_for(noted)["id"] == "n"


def test_global_registry_is_registry():
    assert isinstance(requirements_registry, RequirementRegistry)
