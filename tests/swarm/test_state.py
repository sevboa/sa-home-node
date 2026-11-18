from pathlib import Path

import pytest

from sa_home_node.swarm.kind import traits_for
from sa_home_node.swarm.state import NodeState

FIXTURES = Path(__file__).parent.parent / "fixtures" / "wire"
STATE = FIXTURES / "node-state.json"


def test_roundtrip(tmp_path: Path) -> None:
    state = NodeState.load(STATE)
    assert state.assignments == ["svc", "svc@second"]
    out = tmp_path / "node-state.json"
    state.save(out)
    assert out.read_bytes() == STATE.read_bytes()
    assert not list(tmp_path.glob("*.tmp"))


def test_missing(tmp_path: Path) -> None:
    assert NodeState.load(tmp_path / "none.json") == NodeState()


def test_remember_peer() -> None:
    state = NodeState.load(STATE)
    state.remember_peer("node-b", "tcp://192.0.2.20:8710", ["tcp://192.0.2.20:8710"])
    peer = state.peers[-1]
    assert peer.kind == "server"
    assert peer.endpoints == []
    assert state.peers[-2].id == "node-c"


@pytest.mark.parametrize(
    ("peer_id", "kind", "expected"),
    [("node-d", "server", True), ("node-b", "vps", True), ("node-b", "server", False)],
)
def test_record_peer_kind(peer_id: str, kind: str, expected: bool) -> None:
    state = NodeState.load(STATE)
    assert state.record_peer_kind(peer_id, "tcp://192.0.2.99:8710", [], kind) == expected


def test_peer_kind_unknown() -> None:
    state = NodeState.load(STATE)
    assert state.peer_kind("node-x") == ""


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("server", (True, False, 100)),
        ("vps", (True, False, 50)),
        ("workstation", (False, True, 10)),
        ("", (False, False, 0)),
        (None, (False, False, 0)),
        ("alien", (False, False, 0)),
    ],
)
def test_traits(kind: str | None, expected: tuple[bool, bool, int]) -> None:
    t = traits_for(kind)
    assert (t.always_on, t.wakeable, t.lease_priority) == expected
