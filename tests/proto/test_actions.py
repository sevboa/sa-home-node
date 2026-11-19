from __future__ import annotations

import pytest

from sa_home_node.proto.actions import ActionTable
from sa_home_node.proto.describe import ActionParam, ActionSpec
from sa_home_node.proto.wire import ERR_UNKNOWN_ACTION, ProtoError


async def noop(args: dict) -> dict:
    return {"seen": args}


def spec(action_id: str = "ping", params: tuple[ActionParam, ...] = ()) -> ActionSpec:
    return ActionSpec(id=action_id, title=f"Действие {action_id}", params=params)


async def test_specs_in_registration_order() -> None:
    table = ActionTable()
    table.add(spec("b"), noop)
    table.add(spec("a"), noop)
    table.add(spec("c"), noop)
    assert [s.id for s in table.specs()] == ["b", "a", "c"]


async def test_duplicate_id_raises() -> None:
    table = ActionTable()
    table.add(spec("ping"), noop)
    with pytest.raises(ValueError):
        table.add(spec("ping"), noop)


async def test_run_calls_handler_with_args() -> None:
    table = ActionTable()
    seen: list[dict] = []

    async def handler(args: dict) -> dict:
        seen.append(args)
        return {"ok": True}

    table.add(spec("ping"), handler)
    result = await table.run("ping", {"n": 1})
    assert result == {"ok": True}
    assert seen == [{"n": 1}]


async def test_run_unknown_action_raises_proto_error() -> None:
    table = ActionTable()
    with pytest.raises(ProtoError) as exc:
        await table.run("ghost", {})
    assert exc.value.code == ERR_UNKNOWN_ACTION
    assert "ghost" in exc.value.message


async def test_contains() -> None:
    table = ActionTable()
    table.add(spec("ping"), noop)
    assert "ping" in table
    assert "ghost" not in table
