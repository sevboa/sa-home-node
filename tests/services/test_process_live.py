from __future__ import annotations

import os
import signal
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from support import Collector, install_example_entry_point, wait_for

from sa_home_node.proto.client import ProtoClient
from sa_home_node.proto.wire import Address
from sa_home_node.services.events import EVENT_SERVICE_FAILED, EVENT_SERVICE_STOPPED
from sa_home_node.services.process import ProcessService
from sa_home_node.services.supervisor import process_argv

pytestmark = [pytest.mark.skipif(sys.platform != "linux", reason="unix"), pytest.mark.timeout(60)]

HANG_SCRIPT = Path(__file__).parent.parent / "fixtures" / "hang_service.py"


@pytest.fixture
async def echo(tmp_path, sock_dir, monkeypatch) -> AsyncIterator[tuple[ProcessService, str, Collector]]:
    install_example_entry_point(tmp_path, monkeypatch)
    (tmp_path / "content").mkdir()
    (tmp_path / "content" / "example.toml").write_text('greeting = "Привет"\n')
    sock = os.path.join(sock_dir, "echo.sock")
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[node]\nid = "n"\nsocket = "{sock_dir}/node.sock"\n'
        f'state_path = "{tmp_path}/node-state.json"\nplugins = ["example"]\n'
        f'data_dir = "{tmp_path}/data"\ncontent_dir = "{tmp_path}/content"\n'
        f'[example]\nsocket = "{sock}"\n'
    )
    collector = Collector()
    svc = ProcessService("echo", process_argv("echo", "", str(cfg)), emit=collector, restart_delay_s=0.2, stop_timeout_s=3.0)
    await svc.start()
    try:
        assert await wait_for(lambda: os.path.exists(sock), timeout=15)
        yield svc, sock, collector
    finally:
        await svc.stop()


async def test_echo_answers_restarts_after_kill_and_stops_gracefully(echo) -> None:
    svc, sock, collector = echo
    client = ProtoClient(sock)
    await client.connect()
    try:
        assert await client.command("echo", {"text": "а"}, dst=Address(node="n", service="echo")) == {"text": "а"}
    finally:
        await client.close()
    old = svc.pid
    os.kill(old, signal.SIGKILL)
    assert await wait_for(lambda: svc.restarts == 1 and svc.pid not in (None, old), timeout=15)
    assert collector.of(EVENT_SERVICE_FAILED)[0]["exit_code"] == -9
    started = time.monotonic()
    await svc.stop()
    assert time.monotonic() - started < 3.0
    assert not svc.process_alive
    assert collector.of(EVENT_SERVICE_STOPPED)


async def test_hanging_service_is_killed(tmp_path) -> None:
    ready = tmp_path / "ready"
    svc = ProcessService(
        "hang", [sys.executable, str(HANG_SCRIPT), str(ready)], emit=Collector(), restart_delay_s=0.2, stop_timeout_s=0.5
    )
    await svc.start()
    try:
        assert await wait_for(ready.exists, timeout=15)
        started = time.monotonic()
        await svc.stop()
        assert time.monotonic() - started < 5.0
        assert (svc.process_alive, svc.last_exit_code) == (False, -9)
    finally:
        await svc.stop()
