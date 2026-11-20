from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import Callable

from sa_home_node.config import NodeConfig, NodeSection
from sa_home_node.proto.describe import ActionSpec, ServiceDescription, ServiceInfo
from sa_home_node.proto.server import ProtoServer

TOKEN = "test-token"


async def start_server(endpoint: str, service: StubService) -> ProtoServer:
    server = ProtoServer(endpoint, service, token=TOKEN)
    await server.start()
    return server


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def make_cfg(node_id: str, sock_dir: str, tmp_path, port: int, **swarm) -> NodeConfig:
    return NodeConfig(
        path=tmp_path / "config.toml",
        node=NodeSection(
            id=node_id,
            socket=os.path.join(sock_dir, f"{node_id}.sock"),
            listen=[f"tcp://127.0.0.1:{port}"],
            state_path=str(tmp_path / f"{node_id}.json"),
        ),
        sections={"swarm": {"token": "test-token", "discovery": {"enabled": False}, **swarm}},
    )


class StubService:
    def __init__(
        self,
        node: str = "node-a",
        *,
        service: str = "svc",
        version: str = "0.1.0",
        endpoints: tuple[str, ...] = (),
        node_kind: str = "",
        links: dict | None = None,
        busy: bool = False,
        working: bool | None = False,
        capabilities: tuple[str, ...] = (),
        actions: tuple[ActionSpec, ...] = (ActionSpec(id="noop", title="Ничего"),),
        state: dict | None = None,
    ) -> None:
        self.node = node
        self.service = service
        self.version = version
        self.endpoints = endpoints
        self.node_kind = node_kind
        self.links = links
        self.busy = busy
        self.working = working
        self.capabilities = capabilities
        self.actions = actions
        self.state = state if state is not None else {"node": node}

    def describe(self) -> ServiceDescription:
        return ServiceDescription(
            info=ServiceInfo(
                node=self.node,
                service=self.service,
                version=self.version,
                endpoints=self.endpoints,
                node_kind=self.node_kind,
                links=self.links,
                busy=self.busy,
                working=self.working,
            ),
            capabilities=self.capabilities,
            actions=self.actions,
        )

    async def get_state(self) -> dict:
        return self.state

    async def run_command(self, action: str, args: dict) -> dict:
        return {"accepted": True}


async def wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()
