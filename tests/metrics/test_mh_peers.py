from __future__ import annotations

from types import SimpleNamespace

from sa_home_node.swarm.config import SwarmPeer
from sa_home_node.swarm.node import SwarmApi


def peer(node: str, host: str) -> SwarmPeer:
    return SwarmPeer(id=node, endpoint=f"tcp://{host}:32100")


def test_config_nodes_then_learned_peers() -> None:
    nodes = [peer("n1", "10.0.0.1"), peer("n2", "10.0.0.2")]
    learned = [peer("n3", "10.0.0.3"), peer("n1", "192.168.1.1"), peer("n4", "10.0.0.4")]
    api = SwarmApi(SimpleNamespace(swarm=SimpleNamespace(nodes=nodes), state=SimpleNamespace(peers=learned)))
    got = api.peers()
    assert [(p.id, p.endpoint) for p in got] == [
        ("n1", "tcp://10.0.0.1:32100"),
        ("n2", "tcp://10.0.0.2:32100"),
        ("n3", "tcp://10.0.0.3:32100"),
        ("n1", "tcp://192.168.1.1:32100"),
        ("n4", "tcp://10.0.0.4:32100"),
    ]
    got.clear()
    assert len(api.peers()) == 5
    assert len(nodes) == 2
    assert len(learned) == 3


def test_no_peers() -> None:
    api = SwarmApi(SimpleNamespace(swarm=SimpleNamespace(nodes=[]), state=SimpleNamespace(peers=[])))
    assert api.peers() == []
