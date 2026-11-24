from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from support import free_port, wait_for

from sa_home_node import __version__
from sa_home_node.proto.client import ProtoClient
from sa_home_node.proto.wire import Envelope, ProtoError

pytestmark = [
    pytest.mark.skipif(sys.platform != "linux", reason="unix"),
    pytest.mark.timeout(60),
]

FIXTURES = Path(__file__).parent.parent / "fixtures" / "monitor" / "service"
SRC = Path(__file__).parents[2] / "src"
NODE = "node-a"

BOOT = (
    "import socket, sys\n"
    "from functools import partial\n"
    "node = sys.argv.pop(1)\n"
    "socket.gethostname = lambda: node\n"
    "from sa_home_node.monitor import run\n"
    "run.MetricsEndpoint = partial(run.MetricsEndpoint, address=lambda: '127.0.0.1')\n"
    "from sa_home_node.cli import main\n"
    "raise SystemExit(main(sys.argv[1:]))\n"
)

SENSORS = (
    "[sensors.cpu]\nenabled = false\n[sensors.disks]\nenabled = false\n"
    "[sensors.host]\nenabled = true\nsample_window_s = 0.05\n"
)


class Monitor:
    def __init__(self, root: Path, sock_dir: str, sensors: str = SENSORS, extra: str = "") -> None:
        self.root = root
        self.sock = os.path.join(sock_dir, "monitor.sock")
        self.db = root / "monitor.sqlite"
        self.port = free_port()
        self.events: list[tuple[str, dict]] = []
        self.proc: asyncio.subprocess.Process | None = None
        self.client: ProtoClient | None = None
        (root / "content").mkdir()
        self.config = root / "config.toml"
        self.config.write_text(
            f'[node]\nid = "{NODE}"\n'
            f'data_dir = "{root}/data"\ncontent_dir = "{root}/content"\n'
            f'[monitor]\nsocket = "{self.sock}"\ndb_path = "{self.db}"\n{sensors}{extra}'
        )

    async def on_event(self, env: Envelope) -> None:
        self.events.append((env.payload["event"], env.payload["data"]))

    async def start(self) -> ProtoClient:
        env = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join(filter(None, [str(SRC), os.environ.get("PYTHONPATH", "")])),
        }
        self.proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", BOOT, NODE, "service", "monitor", "--config", str(self.config), env=env
        )
        assert await wait_for(lambda: os.path.exists(self.sock), timeout=20)
        self.client = ProtoClient(self.sock, on_event=self.on_event, timeout=10.0)
        await self.client.connect()
        await _until(self._answers)
        return self.client

    async def _answers(self) -> bool:
        try:
            await self.client.get_state()
        except ProtoError:
            return False
        return True

    async def stop(self) -> int | None:
        if self.client is not None:
            await self.client.close()
        if self.proc is None or self.proc.returncode is not None:
            return None if self.proc is None else self.proc.returncode
        self.proc.send_signal(signal.SIGTERM)
        try:
            return await asyncio.wait_for(self.proc.wait(), timeout=10)
        except TimeoutError:
            self.proc.kill()
            await self.proc.wait()
            raise

    def of(self, name: str) -> list[dict]:
        return [data for kind, data in self.events if kind == name]


@pytest.fixture
def make(tmp_path, sock_dir):
    created: list[Monitor] = []

    def build(**kw) -> Monitor:
        root = tmp_path / f"m{len(created)}"
        root.mkdir()
        created.append(Monitor(root, sock_dir, **kw))
        return created[-1]

    yield build
    for monitor in created:
        if monitor.proc is not None and monitor.proc.returncode is None:
            monitor.proc.kill()


async def _ok_jobs(client: ProtoClient, count: int) -> bool:
    return (await client.get_state())["job_counts"].get("ok", 0) >= count


async def _until(predicate, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.1)
    raise AssertionError("условие не наступило")


@pytest.fixture
async def live(make) -> AsyncIterator[tuple[Monitor, ProtoClient]]:
    monitor = make()
    client = await monitor.start()
    yield monitor, client
    await monitor.stop()


async def test_hello_and_describe_match_reference(live) -> None:
    _, client = live
    expected = json.loads((FIXTURES / "describe.json").read_text())
    assert (await client.hello()).node == NODE
    assert (await client.describe()).to_payload() == {**expected, "version": __version__}


async def test_get_state_keys_and_host_health(live) -> None:
    _, client = live
    reference = json.loads((FIXTURES / "get-state.json").read_text())["ok"]
    state = await client.get_state()
    assert set(state) == set(reference)
    await _until(lambda: _host_ready(client))


async def _host_ready(client: ProtoClient) -> bool:
    return bool((await client.get_state())["host_health"])


async def test_scan_now_runs_jobs_downtime_and_unknown_metric(live) -> None:
    _, client = live
    assert await client.command("scan_now") == {"sensor_queued": True, "smart_queued": True}
    await _until(lambda: _ok_jobs(client, 2))
    page = await client.command("downtime", {"limit": 5})
    assert set(page) == {"events", "offset", "has_next"}
    trend = await client.command("host_trend", {"metric": "no_such_metric"})
    assert trend["error"] == "unknown_metric"
    assert trend["metric"] == "no_such_metric"


async def test_sigterm_exits_cleanly(make) -> None:
    monitor = make()
    await monitor.start()
    started = time.monotonic()
    assert await monitor.stop() == 0
    assert time.monotonic() - started < 10


async def test_seeded_db_keeps_alerting_without_repeat_events(make) -> None:
    monitor = make(sensors=SENSORS.replace("enabled = true", "enabled = false"))
    shutil.copy(FIXTURES / "seeded-monitor.sqlite", monitor.db)
    client = await monitor.start()
    try:
        seeded = json.loads((FIXTURES / "get-state.json").read_text())["ok"]["job_counts"]["ok"]
        health = {h["component_id"]: h for h in (await client.get_state())["health"]}
        assert health["cpu:core0"]["status"] == "alerting"
        await _until(lambda: _ok_jobs(client, seeded + 2))
        await asyncio.sleep(0.5)
        assert monitor.events == []
        assert {h["component_id"]: h["status"] for h in (await client.get_state())["health"]}[
            "cpu:core0"
        ] == "alerting"
    finally:
        await monitor.stop()


async def test_host_degraded_event_reaches_subscriber(make) -> None:
    sensors = SENSORS + (
        "consecutive_to_alert = 1\n[sensors.host.thresholds.disk_used_pct]\nwarn = 0\ncrit = 0\n"
    )
    monitor = make(sensors=sensors)
    client = await monitor.start()
    try:
        await _until(lambda: _host_ready(client))
        await client.command("scan_now")
        assert await wait_for(
            lambda: any(e["metric"] == "disk_used_pct" for e in monitor.of("host_degraded")), timeout=20
        )
    finally:
        await monitor.stop()


async def test_metrics_endpoint_serves_host_families(make) -> None:
    monitor = make(extra="[metrics]\nexport = true\nexporter_port = {PORT}\n")
    monitor.config.write_text(monitor.config.read_text().replace("{PORT}", str(monitor.port)))
    client = await monitor.start()
    try:
        await _until(lambda: _host_ready(client))

        async def body() -> str:
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", monitor.port)
            except OSError:
                return ""
            writer.write(b"GET /metrics HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
            await writer.drain()
            data = await reader.read()
            writer.close()
            return data.decode()

        async def served() -> bool:
            text = await body()
            return "sa_up 1" in text and "sa_host_" in text

        await _until(served)
    finally:
        await monitor.stop()
