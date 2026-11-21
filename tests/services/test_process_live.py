from __future__ import annotations

import os
import signal
import sys
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest
from support import install_example_entry_point, wait_for

from sa_home_node.proto.client import ProtoClient
from sa_home_node.proto.wire import Address
from sa_home_node.services.events import EVENT_SERVICE_FAILED, EVENT_SERVICE_STOPPED
from sa_home_node.services.process import ProcessService
from sa_home_node.services.supervisor import process_argv

pytestmark = [
    pytest.mark.skipif(sys.platform != "linux", reason="unix"),
    pytest.mark.timeout(60),
]

HANG_SCRIPT = Path(__file__).parent.parent / "fixtures" / "hang_service.py"


@pytest.fixture
def example_entry_point(tmp_path, monkeypatch) -> None:
    install_example_entry_point(tmp_path, monkeypatch)


@pytest.fixture
def node_config(tmp_path, sock_dir, example_entry_point) -> tuple[str, str]:
    content = tmp_path / "content"
    content.mkdir()
    (content / "example.toml").write_text('greeting = "Привет"\n')
    sock = os.path.join(sock_dir, "echo.sock")
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[node]\nid = "n"\nsocket = "{sock_dir}/node.sock"\n'
        f'state_path = "{tmp_path}/node-state.json"\nplugins = ["example"]\n'
        f'data_dir = "{tmp_path}/data"\ncontent_dir = "{content}"\n'
        f'[example]\nsocket = "{sock}"\n'
    )
    return str(cfg), sock


@pytest.fixture
def events() -> list[tuple[str, dict]]:
    return []


@pytest.fixture
def emit(events) -> Callable:
    async def _emit(kind: str, data: dict) -> None:
        events.append((kind, data))

    return _emit


@pytest.fixture
async def echo(node_config, emit) -> AsyncIterator[ProcessService]:
    cfg, sock = node_config
    svc = ProcessService(
        "echo",
        process_argv("echo", "", cfg),
        emit=emit,
        restart_delay_s=0.2,
        stop_timeout_s=3.0,
    )
    await svc.start()
    try:
        assert await wait_for(lambda: os.path.exists(sock), timeout=15)
        yield svc
    finally:
        await svc.stop()


async def test_echo_answers_on_socket(echo, node_config) -> None:
    client = ProtoClient(node_config[1])
    await client.connect()
    try:
        result = await client.command("echo", {"text": "а"}, dst=Address(node="n", service="echo"))
    finally:
        await client.close()
    assert result == {"text": "а"}


async def test_echo_restarts_after_kill(echo, events) -> None:
    old = echo.pid
    assert old is not None
    os.kill(old, signal.SIGKILL)
    assert await wait_for(lambda: echo.restarts == 1 and echo.pid not in (None, old), timeout=15)
    failed = [data for kind, data in events if kind == EVENT_SERVICE_FAILED]
    assert failed[0]["exit_code"] == -9


async def test_echo_stops_gracefully(echo, events) -> None:
    started = time.monotonic()
    await echo.stop()
    assert time.monotonic() - started < 3.0
    assert not echo.process_alive
    assert any(kind == EVENT_SERVICE_STOPPED for kind, _ in events)


async def test_hanging_service_is_killed(tmp_path, emit) -> None:
    ready = tmp_path / "ready"
    svc = ProcessService(
        "hang",
        [sys.executable, str(HANG_SCRIPT), str(ready)],
        emit=emit,
        restart_delay_s=0.2,
        stop_timeout_s=0.5,
    )
    await svc.start()
    try:
        assert await wait_for(ready.exists, timeout=15)
        started = time.monotonic()
        await svc.stop()
        assert time.monotonic() - started < 5.0
        assert not svc.process_alive
        assert svc.last_exit_code == -9
    finally:
        await svc.stop()
