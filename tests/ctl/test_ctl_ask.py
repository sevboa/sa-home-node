from __future__ import annotations

import pytest
from ctlfix import load

from sa_home_node.wizard import ask

CASES = load("wizard.json")["cases"]
GENERATED = "GENERATED-TOKEN-0123456789"


def scripted(answers, prompts):
    feed = iter(answers)

    def reply(question: str) -> str:
        prompts.append(question)
        try:
            return next(feed)
        except StopIteration:
            raise EOFError from None

    return reply


def resolve(case, reply):
    value = case["flags"][case["what"]]
    interactive = case["interactive"]
    if case["what"] == "kind":
        return ask.resolve_kind(value, interactive, reply)
    if case["what"] == "assignments":
        return ask.resolve_assignments(value, interactive, reply)
    if case["what"] == "listen":
        return ask.resolve_listen(value, interactive, reply, case["pre"][0])
    if case["what"] == "join":
        return list(ask.resolve_join(value, interactive, reply))
    join, founding = case["pre"]
    return ask.resolve_token(value, interactive, reply, join, founding, lambda: GENERATED)


@pytest.mark.parametrize("case", CASES, ids=[f"{c['what']}-{i}" for i, c in enumerate(CASES)])
def test_resolvers_match_prototype(case, capsys):
    prompts: list[str] = []
    got = resolve(case, scripted(case["answers"], prompts))
    assert got == case["value"]
    assert prompts == case["prompts"]
    assert capsys.readouterr().err == case["stderr"]


@pytest.mark.parametrize(
    ("raw", "items"),
    [("", []), (" a, ,b ", ["a", "b"]), ("x", ["x"])],
)
def test_split_csv(raw, items):
    assert ask.split_csv(raw) == items
