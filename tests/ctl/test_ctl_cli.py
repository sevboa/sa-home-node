from __future__ import annotations

from pathlib import Path

import pytest

from sa_home_node.ctl.cli import build_parser, resolve_target
from sa_home_node.proto.endpoints import parse_endpoint

PARSER = build_parser("nodectl")


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["status"], {"command": "status"}),
        (["start", "monitor"], {"command": "start", "name": "monitor"}),
        (["stop", "monitor"], {"command": "stop", "name": "monitor"}),
        (["restart", "apps"], {"command": "restart", "name": "apps"}),
        (["assign", "apps"], {"command": "assign", "name": "apps"}),
        (["unassign", "apps"], {"command": "unassign", "name": "apps"}),
        (["join", "tcp://h:8710"], {"command": "join", "endpoint": "tcp://h:8710"}),
        (["restart_node"], {"command": "restart_node", "force": False}),
        (["restart_node", "--force"], {"command": "restart_node", "force": True}),
        (["poweroff"], {"command": "poweroff"}),
        (["reboot"], {"command": "reboot"}),
        (["suspend"], {"command": "suspend"}),
        (["events"], {"command": "events"}),
        (
            ["call", "monitor", "scan", "a=1", "b=x"],
            {
                "command": "call",
                "service": "monitor",
                "action": "scan",
                "args": ["a=1", "b=x"],
                "timeout": None,
            },
        ),
        (
            ["call", "tasks", "list", "--timeout", "5"],
            {"command": "call", "service": "tasks", "action": "list", "args": [], "timeout": 5.0},
        ),
        (["describe", "node"], {"command": "describe", "service": "node"}),
        (["check_update"], {"command": "check_update"}),
        (["update"], {"command": "update"}),
        (["-n", "peer1", "status"], {"command": "status", "node": "peer1"}),
        (["--socket", "tcp://h:1", "status"], {"command": "status", "socket": "tcp://h:1"}),
    ],
)
def test_parse(argv, expected):
    args = PARSER.parse_args(argv)
    assert {key: getattr(args, key) for key in expected} == expected


@pytest.mark.parametrize("argv", [[], ["devlab"], ["llm-probe"], ["fix"], ["start"]])
def test_rejects(argv):
    with pytest.raises(SystemExit):
        PARSER.parse_args(argv)


def write_config(tmp_path: Path, socket: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(
        f'[node]\nid = "n1"\nsocket = "{socket}"\n\n[swarm]\ntoken = "secret"\n', encoding="utf-8"
    )
    return path


def test_target_from_config(tmp_path: Path):
    path = write_config(tmp_path, "./data/node.sock")
    args = PARSER.parse_args(["--config", str(path), "status"])
    endpoint, token = resolve_target(args)
    assert endpoint == parse_endpoint(tmp_path / "data" / "node.sock")
    assert token == "secret"


def test_target_socket_flag_wins(tmp_path: Path):
    path = write_config(tmp_path, "./data/node.sock")
    args = PARSER.parse_args(["--config", str(path), "--socket", "tcp://127.0.0.1:9", "status"])
    endpoint, token = resolve_target(args)
    assert endpoint == parse_endpoint("tcp://127.0.0.1:9")
    assert token == "secret"
