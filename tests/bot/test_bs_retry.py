from __future__ import annotations

import logging

import pytest
from botfix import load
from fakebot import make_error, record_sleeps

from sa_home_node.bot import retry

DATA = load("retry/cases.json")


def test_timeout_constant():
    assert retry.REQUEST_TIMEOUT_S == DATA["REQUEST_TIMEOUT_S"]


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: c["name"])
async def test_cases(case, monkeypatch, caplog):
    sleeps = record_sleeps(monkeypatch)
    queue = list(case["script"])
    made: list[Exception] = []
    started = []

    async def factory():
        started.append(1)
        token = queue.pop(0)
        if token == "ok":
            return "result"
        made.append(make_error(token, str(len(started))))
        raise made[-1]

    run = retry.call_with_network_retry(
        factory, what="what-x", attempts=case["attempts"], delay_s=case["delay_s"]
    )
    with caplog.at_level(logging.DEBUG):
        if case["raised"]:
            with pytest.raises(Exception) as info:
                await run
            assert type(info.value).__name__ == case["raised"]["type"]
            assert info.value is made[-1]
        else:
            assert await run == "result"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert (len(started), sleeps, len(warnings)) == (case["calls"], case["sleeps"], case["warnings"])
    assert all("what-x" in r.getMessage() for r in warnings)


async def test_defaults(monkeypatch):
    expected = DATA["defaults"]
    sleeps = record_sleeps(monkeypatch)
    queue = ["net", "net", "ok"]

    async def factory():
        token = queue.pop(0)
        if token == "ok":
            return "result"
        raise make_error(token)

    assert await retry.call_with_network_retry(factory, what="d") == expected["result"]
    assert sleeps == expected["sleeps"]
