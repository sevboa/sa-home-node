from __future__ import annotations

import hashlib

import pytest
from botfix import load
from fakebot import FakeBot, make_error, record_sleeps
from sa_home_node.bot import send

CASES = load("send/cases.json")
CHUNKS = load("send/chunks.json")


def scripted(tokens: list[str]) -> FakeBot:
    bot = FakeBot()
    bot.script["send_message"] = [
        None if t == "ok" else make_error(t, str(i + 1)) for i, t in enumerate(tokens)
    ]
    return bot


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: c["name"])
async def test_send_with_retry(case, monkeypatch):
    sleeps = record_sleeps(monkeypatch)
    bot = scripted(case["script"])
    outcome: dict = {}
    result = await send.send_with_retry(lambda: bot.send_message(1001, "x"), outcome=outcome)
    assert (result is not None) == (case["result"] is not None)
    assert (len(bot.of("send_message")), sleeps, outcome) == (case["calls"], case["sleeps"], case["outcome"])


async def test_without_outcome(monkeypatch):
    sleeps = record_sleeps(monkeypatch)
    bot = scripted(["net", "ok"])
    assert await send.send_with_retry(lambda: bot.send_message(1, "a")) is not None
    assert sleeps == CASES["without_outcome"]["sleeps"]


@pytest.mark.parametrize("case", CASES["errors"], ids=lambda c: c["token"])
def test_error_kinds(case):
    exc = make_error(case["token"], "x")
    assert send.is_parse_error(exc) is case["parse_error"]
    assert send.is_permanent_send_error(exc) is case["permanent"]


def test_max_len():
    assert send.MAX_LEN == CASES["MAX_LEN"]


@pytest.mark.parametrize("case", CHUNKS["cases"], ids=lambda c: c["name"])
def test_chunks_at_limit(case):
    text = "".join(ch * n for ch, n in case["parts"])
    chunks = send.chunk_text(text, send.MAX_LEN)
    assert [[len(c), hashlib.sha256(c.encode()).hexdigest()] for c in chunks] == case["chunks"]
    assert send.chunk_text(text) == chunks


@pytest.mark.parametrize("case", CHUNKS["small"], ids=lambda c: repr(c["text"]))
def test_chunks_small_limit(case):
    assert send.chunk_text(case["text"], case["limit"]) == case["chunks"]
