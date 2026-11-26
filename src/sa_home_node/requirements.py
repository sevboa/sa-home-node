from __future__ import annotations

import shutil
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock

from sa_home_node import host

_PACKAGE_MANAGERS: tuple[tuple[str, str], ...] = (
    ("apt", "sudo apt install {pkg}"),
    ("pacman", "sudo pacman -S {pkg}"),
    ("dnf", "sudo dnf install {pkg}"),
    ("zypper", "sudo zypper install {pkg}"),
    ("apk", "sudo apk add {pkg}"),
    ("brew", "brew install {pkg}"),
    ("winget", "winget install {pkg}"),
)

_PERMISSION_MARKERS = (
    "permission denied",
    "operation not permitted",
    "must be root",
    "requires root",
    "eacces",
    "access is denied",
)

SUDO_PASSWORD_MARKER = "a password is required"


def looks_like_permission_error(stderr: str) -> bool:
    low = stderr.lower()
    return any(marker in low for marker in _PERMISSION_MARKERS)


def looks_like_sudo_denied(stderr: str) -> bool:
    return looks_like_permission_error(stderr) or SUDO_PASSWORD_MARKER in stderr.lower()


class RequirementStatus(StrEnum):
    OK = "ok"
    MISSING_PROGRAM = "missing_program"
    UNSUPPORTED_PLATFORM = "unsupported_platform"
    NEEDS_PRIVILEGE = "needs_privilege"


def _install_command(package: str) -> str:
    for manager, template in _PACKAGE_MANAGERS:
        if shutil.which(manager) is not None:
            return template.format(pkg=package)
    return f"установите пакет {package!r} через пакетный менеджер системы"


@dataclass(frozen=True)
class Requirement:
    program: str | None = None
    package: str = ""
    platforms: tuple[str, ...] | None = None
    note: str = ""
    key: str | None = None

    def _wrong_platform(self) -> bool:
        return self.platforms is not None and host.current().name not in self.platforms

    def diagnose(self) -> RequirementStatus:
        if self._wrong_platform():
            return RequirementStatus.UNSUPPORTED_PLATFORM
        if self.program is not None and shutil.which(self.program) is None:
            return RequirementStatus.MISSING_PROGRAM
        return RequirementStatus.OK

    def available(self) -> bool:
        return self.diagnose() is RequirementStatus.OK

    def install_hint(self) -> str:
        if self._wrong_platform():
            where = " / ".join(self.platforms or ())
            base = f"не поддерживается на этой ОС (пока есть только для {where})"
        else:
            base = f"{_install_command(self.package)} (нужна команда {self.program!r})"
        return f"{base} — {self.note}" if self.note else base

    def privilege_hint(self) -> str:
        base = f"не хватает прав на команду {self.program!r} — выполните на ноде `nodectl fix`"
        return f"{base} — {self.note}" if self.note else base


class RequirementRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, RequirementStatus] = {}
        self._lock = Lock()

    @staticmethod
    def _key(requirement: Requirement) -> str:
        return requirement.key or requirement.program or requirement.note or repr(requirement)

    def report(self, requirement: Requirement, status: RequirementStatus) -> None:
        with self._lock:
            self._entries[self._key(requirement)] = status

    def status_for(self, requirement: Requirement) -> RequirementStatus:
        static = requirement.diagnose()
        if static is not RequirementStatus.OK:
            return static
        with self._lock:
            reported = self._entries.get(self._key(requirement))
        if reported is RequirementStatus.NEEDS_PRIVILEGE:
            return RequirementStatus.NEEDS_PRIVILEGE
        return RequirementStatus.OK

    def problem_for(self, requirement: Requirement) -> dict | None:
        status = self.status_for(requirement)
        if status is RequirementStatus.OK:
            return None
        hint = (
            requirement.privilege_hint()
            if status is RequirementStatus.NEEDS_PRIVILEGE
            else requirement.install_hint()
        )
        return {
            "id": requirement.key or requirement.program or requirement.note,
            "status": status.value,
            "hint": hint,
        }

    def reset(self) -> None:
        with self._lock:
            self._entries.clear()


requirements_registry = RequirementRegistry()
