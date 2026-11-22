from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

from sa_home_node.config import NodeConfig, load_config
from sa_home_node.swarm.state import NodeState

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "fixups"

ROOT_PREFIXES = ("/etc/", "/usr/", "/proc/", "/sbin/", "/bin/", "/var/", "/sys/")


def golden_bytes(name: str) -> bytes:
    return (GOLDEN / name).read_bytes()


def golden_json(name: str):
    return json.loads(golden_bytes(name))


def dump(obj) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def assert_golden(actual, expected) -> None:
    assert actual == expected
    assert dump(actual) == dump(expected)


def make_config(tmp_path: Path, node: str = "", state: list[str] | None = None) -> NodeConfig:
    path = tmp_path / "config.toml"
    state_path = tmp_path / "state.json"
    path.write_text(f'[node]\nid = "node-a"\nstate_path = "{state_path}"\n{node}\n[sec]\nk = 5\n')
    if state is not None:
        NodeState(assignments=state).save(state_path)
    return load_config(path)


class FakeRoot:
    def __init__(self, monkeypatch):
        self.files: dict[str, str] = {}
        self.dirs: set[str] = set()
        self.missing_tools: set[str] = set()
        self.active_units: set[str] = set()
        self.outputs: dict[str, str | None] = {}
        self.calls: list[list[str]] = []
        self.installed: dict[str, str] = {}
        self.denied: set[str] = set()
        self.reject_visudo = False
        monkeypatch.setattr(subprocess, "run", self._run)
        monkeypatch.setattr(shutil, "which", self._which)
        fakes = {
            "exists": self._known,
            "is_file": lambda p: str(p) in self.files,
            "is_dir": lambda p: str(p) in self.dirs,
        }
        for name, fake in fakes.items():
            monkeypatch.setattr(Path, name, self._patched(getattr(Path, name), fake))
        monkeypatch.setattr(Path, "read_text", self._read_text(Path.read_text))

    @staticmethod
    def _patched(real, fake):
        return lambda p: fake(p) if str(p).startswith(ROOT_PREFIXES) else real(p)

    def _known(self, path) -> bool:
        if str(path) in self.denied:
            raise PermissionError(str(path))
        return str(path) in self.files or str(path) in self.dirs

    def _read_text(self, real):
        def read(path, *args, **kwargs):
            if not str(path).startswith(ROOT_PREFIXES):
                return real(path, *args, **kwargs)
            if str(path) not in self.files:
                raise FileNotFoundError(str(path))
            return self.files[str(path)]

        return read

    def _which(self, name, mode=None, path=None):
        return None if name in self.missing_tools else f"/usr/bin/{name}"

    @staticmethod
    def _result(returncode=0, stdout="", stderr=""):
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    def _run(self, argv, **kwargs):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        stripped = self.strip_sudo(argv)
        key = " ".join(stripped)
        if key in self.outputs:
            output = self.outputs[key]
            return self._result(0 if output is not None else 1, output or "")
        handler = getattr(self, f"_cmd_{stripped[0].rsplit('/', 1)[-1]}", None)
        return handler(stripped) if handler is not None else self._result()

    def _cmd_install(self, argv):
        if "-d" in argv:
            return self._result()
        source, dest = argv[-2], argv[-1]
        self.installed[dest] = Path(source).read_text()
        self.files[dest] = self.installed[dest]
        return self._result()

    def _cmd_cat(self, argv):
        content = self.files.get(argv[1])
        return self._result(0 if content is not None else 1, content or "")

    def _cmd_test(self, argv):
        return self._result(0 if argv[2] in self.files or argv[2] in self.dirs else 1)

    def _cmd_visudo(self, argv):
        return self._result(1, stderr="syntax error") if self.reject_visudo else self._result()

    def _cmd_systemctl(self, argv):
        if "is-active" in argv:
            return self._result(0 if argv[-1] in self.active_units else 3)
        return self._result()

    @staticmethod
    def strip_sudo(argv: list[str]) -> list[str]:
        return argv[1:] if argv[0] == "sudo" else argv

    @property
    def sudo_calls(self) -> list[list[str]]:
        return [self.strip_sudo(c) for c in self.calls if c[0] == "sudo"]

    def sudo_commands(self, command: str) -> list[list[str]]:
        return [c for c in self.sudo_calls if c[0] == command]
