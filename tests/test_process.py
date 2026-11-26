from __future__ import annotations

import asyncio
import shutil
import sys

import pytest

from sa_home_node import host
from sa_home_node.process import run, run_privileged
from sa_home_node.proto.wire import ERR_INTERNAL, ERR_NEEDS_PRIVILEGE, ProtoError

PY = sys.executable


def _py(code: str) -> list[str]:
    return [PY, "-c", code]


@pytest.mark.parametrize(
    ("code", "data", "out", "err", "rc"),
    [
        ("print('hello')", None, b"hello\n", b"", 0),
        ("import sys; sys.stderr.write(' err ')", None, b"", b" err ", 0),
        ("import sys; sys.exit(42)", None, b"", b"", 42),
        ("import sys; sys.stdout.write(sys.stdin.read())", b"input", b"input", b"", 0),
    ],
)
async def test_run(code: str, data: bytes | None, out: bytes, err: bytes, rc: int) -> None:
    result = await run(_py(code), input=data)
    assert (result.returncode, result.stdout, result.stderr) == (rc, out, err)
    assert result.stderr_text == err.decode().strip()


async def test_run_timeout_kills_process(tmp_path) -> None:
    marker = tmp_path / "done"
    with pytest.raises(asyncio.TimeoutError):
        await run(
            _py(f"import time, pathlib; time.sleep(2); pathlib.Path({str(marker)!r}).touch()"), timeout=0.2
        )
    await asyncio.sleep(2.5)
    assert not marker.exists()


class FakeHost:
    def __init__(self, elevate: bool) -> None:
        self.elevate = elevate
        self.seen: list[str] | None = None

    def elevate_noninteractive_argv(self, argv: list[str]) -> list[str] | None:
        self.seen = argv
        return list(argv) if self.elevate else None


@pytest.fixture
def fake_host(monkeypatch: pytest.MonkeyPatch) -> FakeHost:
    fake = FakeHost(elevate=True)
    monkeypatch.setattr(host, "current", lambda: fake)
    return fake


async def test_run_privileged_resolves_program_and_returns_stdout(fake_host: FakeHost) -> None:
    out = await run_privileged(["python3", "-c", "print('ok')"], needs_privilege_text="нужны права")
    assert out == "ok\n"
    assert fake_host.seen is not None and fake_host.seen[0] == shutil.which("python3")


@pytest.mark.parametrize(
    ("stderr", "code", "message"),
    [
        ("sudo: a password is required", ERR_NEEDS_PRIVILEGE, "нужны права"),
        ("Permission denied", ERR_NEEDS_PRIVILEGE, "нужны права"),
        ("some error", ERR_INTERNAL, "завершился ошибкой: some error"),
    ],
)
async def test_run_privileged_failure(fake_host: FakeHost, stderr: str, code: str, message: str) -> None:
    with pytest.raises(ProtoError) as exc:
        await run_privileged(
            _py(f"import sys; sys.stderr.write({stderr!r}); sys.exit(1)"), needs_privilege_text="нужны права"
        )
    assert exc.value.code == code
    assert exc.value.message.endswith(message)


async def test_run_privileged_without_elevation(fake_host: FakeHost) -> None:
    fake_host.elevate = False
    with pytest.raises(ProtoError) as exc:
        await run_privileged(_py("print('ok')"), needs_privilege_text="нужны права")
    assert (exc.value.code, exc.value.message) == (ERR_NEEDS_PRIVILEGE, "нужны права")
