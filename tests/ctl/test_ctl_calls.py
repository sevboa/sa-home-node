from __future__ import annotations

import pytest
from ctlfix import load

from sa_home_node.ctl.calls import parse_call_args, render_describe
from sa_home_node.proto.describe import ServiceDescription

DATA = load("calls.json")


@pytest.mark.parametrize("case", DATA["call_args"], ids=lambda c: " ".join(c["pairs"]) or "empty")
def test_call_args(case):
    if "error" in case:
        with pytest.raises(ValueError) as exc:
            parse_call_args(case["pairs"])
        assert str(exc.value) == case["error"]
    else:
        got = parse_call_args(case["pairs"])
        assert got == case["args"]
        assert [type(v) for v in got.values()] == [type(v) for v in case["args"].values()]


@pytest.mark.parametrize("case", DATA["describe"], ids=lambda c: c["payload"]["service"])
def test_describe_text(case):
    assert render_describe(ServiceDescription.from_payload(case["payload"])) == case["text"]
