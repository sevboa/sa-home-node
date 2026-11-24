from __future__ import annotations

import asyncio
import json
import shutil
import socket
from pathlib import Path

import pytest

from sa_home_node import promexport
from sa_home_node.monitor.config import MetricsConfig
from sa_home_node.monitor.db import Database
from sa_home_node.monitor.exposition import render_metrics, store_renderer
from sa_home_node.monitor.hostmodels import HostMetricState
from sa_home_node.monitor.models import HealthState
from sa_home_node.monitor.store import MonitorStore
from sa_home_node.promexport import MetricsEndpoint

FIXTURES = Path(__file__).parents[1] / "fixtures" / "monitor" / "service"
GOLDEN = json.loads((FIXTURES / "monitor-exposition.json").read_text())


def _health(rows):
    return [HealthState(**row) for row in rows]


def _hosts(rows):
    return [HostMetricState(**row) for row in rows]


@pytest.mark.parametrize("name", list(GOLDEN["render_metrics"]))
def test_render_metrics(name):
    case = GOLDEN["render_metrics"][name]
    assert render_metrics(_health(case["health"]), _hosts(case["host_states"])) == case["text"]


@pytest.mark.parametrize("name", list(GOLDEN["format_labels"]))
def test_format_labels(name):
    case = GOLDEN["format_labels"][name]
    assert promexport.format_labels(case["labels"]) == case["text"]


def test_sample():
    for case in GOLDEN["sample"]:
        assert promexport.sample(case["name"], case["labels"], case["value"]) == case["text"]


@pytest.mark.parametrize("name", list(GOLDEN["gauge_family"]))
def test_gauge_family(name):
    expected = GOLDEN["gauge_family"][name]
    samples = [line for line in expected if not line.startswith("#")]
    title = expected[0].split(" ", 3)[3]
    assert promexport.gauge_family("sa_x", title, samples) == expected


def test_metric_name():
    for case in GOLDEN["metric_name"]:
        assert bool(promexport.METRIC_NAME.match(case["name"])) is case["matches"]


async def test_store_renderer_seeded(tmp_path):
    path = tmp_path / "monitor.sqlite"
    shutil.copy(FIXTURES / "seeded-monitor.sqlite", path)
    db = Database(path)
    await db.open()
    try:
        assert await store_renderer(MonitorStore(db))() == GOLDEN["store_renderer_seeded"]
    finally:
        await db.close()


def test_metrics_config():
    assert MetricsConfig().export is False
    assert MetricsConfig().exporter_port == 9100
    assert MetricsConfig.model_validate({"hub": True}).export is False
    for port in (0, 65536):
        with pytest.raises(ValueError):
            MetricsConfig(exporter_port=port)


async def _get(port, path):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
    await writer.drain()
    raw = await reader.read()
    writer.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    return head.decode(), body.decode()


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _wait_listening(endpoint):
    for _ in range(100):
        if endpoint._server is not None:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("endpoint did not bind")


async def test_endpoint_serves_metrics():
    async def render():
        return "sa_up 1\n"

    port = _free_port()
    endpoint = MetricsEndpoint(render, port, address=lambda: "127.0.0.1", retry_s=0.05)
    await endpoint.start()
    try:
        await _wait_listening(endpoint)
        head, body = await _get(port, promexport.METRICS_PATH)
        assert head.startswith("HTTP/1.1 200")
        assert f"Content-Type: {promexport.CONTENT_TYPE}" in head
        assert body == "sa_up 1\n"
        head, _ = await _get(port, "/other")
        assert head.startswith("HTTP/1.1 404")
    finally:
        await endpoint.stop()


async def test_endpoint_retries_without_address():
    calls = []

    def address():
        calls.append(1)
        return "127.0.0.1" if len(calls) >= 3 else None

    async def render():
        return "x"

    endpoint = MetricsEndpoint(render, _free_port(), address=address, retry_s=0.01)
    await endpoint.start()
    try:
        await _wait_listening(endpoint)
        assert len(calls) == 3
    finally:
        await endpoint.stop()


async def test_endpoint_stop_without_address():
    async def render():
        return "x"

    endpoint = MetricsEndpoint(render, _free_port(), address=lambda: None, retry_s=0.01)
    await endpoint.start()
    await asyncio.sleep(0.05)
    await endpoint.stop()
    assert endpoint._server is None
