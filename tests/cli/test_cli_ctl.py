from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

from sa_home_node import cli


@pytest.mark.parametrize(
    ("argv", "expected_keys"),
    [
        (["ctl", "status"], {"command": "status"}),
        (["ctl", "start", "monitor"], {"command": "start", "name": "monitor"}),
        (["ctl", "stop", "monitor"], {"command": "stop", "name": "monitor"}),
        (["ctl", "restart", "apps"], {"command": "restart", "name": "apps"}),
        (["ctl", "join", "tcp://h:8710"], {"command": "join", "endpoint": "tcp://h:8710"}),
        (["ctl", "restart_node"], {"command": "restart_node", "force": False}),
        (["ctl", "restart_node", "--force"], {"command": "restart_node", "force": True}),
        (
            ["ctl", "call", "monitor", "scan", "a=1", "b=x"],
            {
                "command": "call",
                "service": "monitor",
                "action": "scan",
                "args": ["a=1", "b=x"],
                "timeout": None,
            },
        ),
        (
            ["ctl", "call", "tasks", "list", "--timeout", "5"],
            {"command": "call", "service": "tasks", "action": "list", "args": [], "timeout": 5.0},
        ),
        (["ctl", "describe", "node"], {"command": "describe", "service": "node"}),
        (["ctl", "check_update"], {"command": "check_update"}),
        (["ctl", "update"], {"command": "update"}),
        (["ctl", "-n", "peer1", "status"], {"command": "status", "node": "peer1"}),
        (["ctl", "--socket", "tcp://h:1", "status"], {"command": "status", "socket": "tcp://h:1"}),
        (
            ["ctl", "--config", "/path/to/config.toml", "status"],
            {"command": "status", "config": Path("/path/to/config.toml")},
        ),
    ],
)
def test_ctl_passes_parsed_args_to_run(monkeypatch, argv, expected_keys):
    recorded = {"args": None}
    return_code = 7

    async def fake_run(args: Namespace) -> int:
        recorded["args"] = args
        return return_code

    monkeypatch.setattr("sa_home_node.ctl.run.run", fake_run)
    assert cli.main(argv) == return_code

    assert recorded["args"] is not None
    for key, expected_value in expected_keys.items():
        assert getattr(recorded["args"], key) == expected_value, (
            f"mismatch on {key}: {getattr(recorded['args'], key)} != {expected_value}"
        )


def test_ctl_no_subcommand_exits(monkeypatch, capsys):
    recorded = {"args": None}

    async def fake_run(args: Namespace) -> int:
        recorded["args"] = args
        return 0

    monkeypatch.setattr("sa_home_node.ctl.run.run", fake_run)
    with pytest.raises(SystemExit):
        cli.main(["ctl"])
    assert recorded["args"] is None
