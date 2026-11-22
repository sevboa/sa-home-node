from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel
from world import assert_golden, golden_bytes, golden_json, make_config

from sa_home_node.fixups import needs
from sa_home_node.fixups.core import Fixup, FixupContext, FixupError, run_fixups
from sa_home_node.host import select


def fx(fid: str, check, apply=lambda: None) -> Fixup:
    return Fixup(fid, f"T-{fid}", check, apply)


def raiser(exc: Exception):
    def run():
        raise exc

    return run


def context(tmp_path: Path, node: str = "", state: list[str] | None = None) -> FixupContext:
    return FixupContext.build(make_config(tmp_path, node, state), select("linux"))


def test_apply_os_error_fails_fixup_and_continues() -> None:
    lines: list[str] = []
    report = run_fixups([fx("a", lambda: False, raiser(OSError("нет"))), fx("b", lambda: True)], out=lines.append)
    assert lines[1:] == ["  ❌ T-a: нет", "  ✅ T-b — уже применено"]
    assert report.failed == ["a"] and report.already == ["b"]


def test_dry_run_never_applies() -> None:
    calls: list[str] = []
    lines: list[str] = []
    fixups = [
        fx("a", lambda: False, lambda: calls.append("apply")),
        fx("b", lambda: True),
        fx("c", raiser(FixupError("нет sudo"))),
    ]
    report = run_fixups(fixups, dry_run=True, out=lines.append)
    assert calls == []
    assert lines == [
        "  🔍 T-a — нужно применить",
        "  ✅ T-b — уже применено",
        "  ⚠️ T-c: проверка не удалась (нет sudo)",
        "  🔍 T-c — нужно применить",
    ]
    assert report.pending == ["a", "c"] and report.already == ["b"]


def test_context_effective_assignments(tmp_path: Path) -> None:
    ctx = context(tmp_path, 'assignments = ["svc", "bad name"]', ["svc-s:standby", "svc"])
    assert [a.service for a in ctx.assignments] == ["svc", "svc-s"]
    assert ctx.assigned("svc-s") and ctx.assigned("x", "svc") and not ctx.assigned("x")
    assert ctx.host.name == "linux"


def test_context_without_state_and_section(tmp_path: Path) -> None:
    class Sec(BaseModel):
        k: int = 0

    ctx = context(tmp_path)
    assert ctx.assignments == ()
    assert ctx.section("sec", Sec).k == 5


@pytest.mark.parametrize(
    ("node", "power", "wake"),
    [
        ('kind = "server"\npower_controllable = true', True, False),
        ('kind = "workstation"\npower_controllable = false', False, True),
    ],
)
def test_needs_power_override(tmp_path: Path, node: str, power: bool, wake: bool) -> None:
    ctx = context(tmp_path, node)
    assert needs.power_controllable(ctx) is power
    assert needs.wakeable(ctx) is wake


def test_needs_all_of(tmp_path: Path) -> None:
    ctx = context(tmp_path, 'kind = "workstation"\nassignments = ["svc"]')
    assert needs.assigned("x", "svc")(ctx)
    assert needs.all_of(needs.assigned("svc"), needs.wakeable)(ctx)
    assert not needs.all_of(needs.assigned("svc"), needs.assigned("svc-s"))(ctx)
    assert needs.all_of()(ctx)


def parse_outcome(raw):
    if isinstance(raw, bool):
        return raw
    kind, _, message = raw.partition(":")
    return {"FixupError": FixupError, "OSError": OSError}[kind](message)


def spec_fixup(spec: dict) -> Fixup:
    checks = iter(spec["check"])

    def check() -> bool:
        outcome = parse_outcome(next(checks))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def apply() -> None:
        if spec["apply"] != "ok":
            raise parse_outcome(spec["apply"])

    return Fixup(spec["id"], spec["title"], check, apply)


RUNS = golden_json("runs.json")
NEEDS = golden_json("needs.json")


def run_spec(run: dict, lines: list[str]):
    return run_fixups([spec_fixup(s) for s in run["fixups"]], out=lines.append)


@pytest.mark.parametrize("run", RUNS, ids=[r["name"] for r in RUNS])
def test_run_fixups_golden(run: dict) -> None:
    lines: list[str] = []
    report = run_spec(run, lines)
    assert_golden({"failed": report.failed, "output": lines}, {"failed": run["failed"], "output": run["output"]})


def test_run_output_golden_bytes() -> None:
    lines: list[str] = []
    for run in RUNS:
        run_spec(run, lines)
    assert "".join(f"{line}\n" for line in lines).encode("utf-8") == golden_bytes("run-output.txt")


@pytest.mark.parametrize("row", NEEDS, ids=[f"{r['kind']}-{'+'.join(r['assignments']) or 'none'}" for r in NEEDS])
def test_needs_golden(tmp_path: Path, row: dict) -> None:
    quoted = ", ".join(json.dumps(a) for a in row["assignments"])
    ctx = context(tmp_path, f'kind = "{row["kind"]}"\nassignments = [{quoted}]')
    actual = {
        **row,
        "power_controllable": needs.power_controllable(ctx),
        "wakeable": needs.wakeable(ctx),
        "assigned": {s: needs.assigned(s)(ctx) for s in row["assigned"]},
    }
    assert_golden(actual, row)
