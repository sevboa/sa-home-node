from __future__ import annotations

import json
from pathlib import Path

import pytest
from support import TOKEN, StubService, free_port, start_server

from sa_home_node.ctl.cli import build_parser
from sa_home_node.ctl.run import run
from sa_home_node.proto.describe import ActionParam, ActionSpec
from sa_home_node.proto.wire import ProtoError

SERVICE = {"name": "monitor", "status": "running", "pid": 5, "restarts": 0}
OTHER_ACTIONS = ("assign", "unassign", "join", "scan", "check_update", "update", "restart_node", "poweroff")
STATE = {"node": "n1", "version": "1.0", "services": [SERVICE]}


class NodeStub(StubService):
    def __init__(self, replies: dict) -> None:
        super().__init__(
            "n1",
            service="node",
            actions=(
                ActionSpec("start", "Запустить", (ActionParam("name", "str"),)),
                *(ActionSpec(name, name) for name in OTHER_ACTIONS),
            ),
            state=STATE,
        )
        self.replies = replies
        self.calls: list[tuple[str, dict]] = []

    async def run_command(self, action: str, args: dict) -> dict:
        self.calls.append((action, args))
        reply = self.replies.get(action, {})
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
async def node(tmp_path: Path):
    port = free_port()
    stub = NodeStub({})
    server = await start_server(f"tcp://127.0.0.1:{port}", stub)
    config = tmp_path / "config.toml"
    config.write_text(
        f'[node]\nid = "n1"\nsocket = "tcp://127.0.0.1:{port}"\n\n[swarm]\ntoken = "{TOKEN}"\n',
        encoding="utf-8",
    )
    yield stub, config
    await server.stop()


async def call(config: Path, *argv: str) -> int:
    return await run(build_parser("nodectl").parse_args(["--config", str(config), *argv]))


async def test_status(node, capsys):
    _, config = node
    assert await call(config, "status") == 0
    out = capsys.readouterr().out
    assert out.startswith("Нода n1 (v1.0)\n")
    assert "monitor" in out


@pytest.mark.parametrize(
    ("argv", "reply", "action", "args", "text"),
    [
        (["start", "monitor"], {"service": SERVICE}, "start", {"name": "monitor"}, "СЛУЖБА"),
        (["assign", "monitor"], {"service": SERVICE}, "assign", {"name": "monitor"}, "🟢 running"),
        (["unassign", "apps"], {"unassigned": "apps"}, "unassign", {"name": "apps"}, "Снято: apps"),
        (["unassign", "apps"], {}, "unassign", {"name": "apps"}, "Снято: apps"),
        (
            ["join", "tcp://h:1"],
            {"peers_added": ["a", "b"]},
            "join",
            {"endpoint": "tcp://h:1"},
            "Присоединились через tcp://h:1. Новых пиров: a, b.",
        ),
        (["join", "tcp://h:1"], {}, "join", {"endpoint": "tcp://h:1"}, "Новых пиров: нет."),
        (["call", "node", "scan", "a=1", "b=x"], {"ok": True}, "scan", {"a": 1, "b": "x"}, '"ok": true'),
        (
            ["check_update"],
            {"repo": "r", "running": "1", "installed": "1", "latest": "1"},
            "check_update",
            {},
            "Установлена последняя версия.",
        ),
        (["update"], {"up_to_date": True, "version": "2"}, "update", {}, "Уже последняя версия v2."),
        (
            ["restart_node"],
            {"scheduled": "restart_node", "delay_s": 3},
            "restart_node",
            {},
            "Принято: нода выполнит restart_node через 3 с.",
        ),
        (
            ["restart_node", "--force"],
            {"scheduled": "restart_node", "delay_s": 0},
            "restart_node",
            {"force": True},
            "через 0 с.",
        ),
        (
            ["restart_node"],
            {"deferred": 120, "message": "рестарт отложен до освобождения служб"},
            "restart_node",
            {},
            "нода: рестарт отложен до освобождения служб.",
        ),
        (
            ["poweroff"],
            {"scheduled": "poweroff", "delay_s": 5},
            "poweroff",
            {},
            "выполнит poweroff через 5 с.",
        ),
    ],
)
async def test_commands(node, capsys, argv, reply, action, args, text):
    stub, config = node
    stub.replies[action] = reply
    assert await call(config, *argv) == 0
    assert stub.calls == [(action, args)]
    assert text in capsys.readouterr().out


async def test_call_prints_json(node, capsys):
    stub, config = node
    stub.replies["scan"] = {"a": [1, 2], "текст": "да"}
    assert await call(config, "call", "node", "scan") == 0
    assert json.loads(capsys.readouterr().out) == {"a": [1, 2], "текст": "да"}


async def test_describe(node, capsys):
    _, config = node
    assert await call(config, "describe", "node") == 0
    out = capsys.readouterr().out
    assert out.startswith("Служба node на n1 (v0.1.0)\n  start            Запустить  (name:str)\n")
    assert "  poweroff         poweroff\n" in out


async def test_proto_error(node, capsys):
    stub, config = node
    stub.replies["start"] = ProtoError("bad_request", "нет такой службы")
    assert await call(config, "start", "nope") == 1
    assert "Ошибка: нет такой службы" in capsys.readouterr().err


async def test_bad_call_args(node, capsys):
    stub, config = node
    assert await call(config, "call", "node", "scan", "oops") == 2
    assert stub.calls == []
    assert "аргумент без «=»: 'oops'" in capsys.readouterr().err


async def test_unreachable(tmp_path: Path, capsys):
    config = tmp_path / "config.toml"
    config.write_text(
        f'[node]\nid = "n1"\nsocket = "tcp://127.0.0.1:{free_port()}"\n\n[swarm]\ntoken = "t"\n',
        encoding="utf-8",
    )
    assert await call(config, "status") == 1
    assert capsys.readouterr().err.startswith("Нода недоступна (")
