from __future__ import annotations

import stat
from datetime import datetime

from sa_home_node.backup import hold


def test_marker_next_to_state(tmp_path):
    state = tmp_path / "data" / "node.sqlite"
    assert hold.marker_path(state) == tmp_path / "data" / "backup-publish.ok"
    assert hold.marker_path(str(state)) == hold.marker_path(state)


def test_allow_publish(tmp_path):
    state = tmp_path / "data" / "node.sqlite"
    assert not hold.publish_allowed(state)
    path = hold.allow_publish(state, "первый запуск")
    assert path == hold.marker_path(state)
    assert hold.publish_allowed(state)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    stamp, reason = path.read_text(encoding="utf-8").rstrip("\n").split(" ", 1)
    assert reason == "первый запуск"
    assert datetime.fromisoformat(stamp).tzinfo is not None
    hold.allow_publish(state, "снова")
    assert path.read_text(encoding="utf-8").endswith(" снова\n")


def test_hint():
    assert "backup-publish.ok" in hold.HOLD_HINT
    assert "nodectl backup-release" in hold.HOLD_HINT
