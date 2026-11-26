from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sa_home_node.toml_emit import toml_table


@dataclass(frozen=True)
class NodeAnswers:
    node_id: str
    kind: str
    assignments: list[str]
    listen: list[str]
    token: str
    join: str


def render_config(answers: NodeAnswers, workdir: Path) -> str:
    data_dir = workdir / "data"
    node_values = {
        "id": answers.node_id,
        "kind": answers.kind,
        "data_dir": str(data_dir),
        "socket": str(data_dir / "node.sock"),
        "state_path": str(data_dir / "node-state.json"),
        "assignments": answers.assignments,
        "listen": answers.listen,
    }
    swarm_values = {
        "token": answers.token,
        "join": answers.join,
    }
    return toml_table("node", node_values) + "\n" + toml_table("swarm", swarm_values)


def render_systemd_unit(executable: str, config_path: Path, workdir: Path) -> str:
    local_bin = str(Path.home() / ".local" / "bin")
    return (
        "[Unit]\n"
        "Description=sa-home-node (нода: супервизор служб роя)\n"
        "After=network.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"WorkingDirectory={workdir}\n"
        f"ExecStart={executable} run --config {config_path}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "TimeoutStartSec=300\n"
        "KillSignal=SIGTERM\n"
        "TimeoutStopSec=150\n"
        f"Environment=PATH={local_bin}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
