from __future__ import annotations

import stat

import pytest

from sa_home_node.metrics.writeout import write_changed


def _files(tmp_path):
    return {
        tmp_path / "targets" / "nodes.json": "[]\n",
        tmp_path / "dashboards" / "nodes.json": "{}\n",
    }


def test_first_call_writes_everything(tmp_path):
    files = _files(tmp_path)
    assert write_changed(files) == list(files)
    for path, content in files.items():
        assert path.read_text(encoding="utf-8") == content
        assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_second_call_writes_nothing(tmp_path):
    files = _files(tmp_path)
    write_changed(files)
    assert write_changed(files) == []


def test_only_changed_file_is_written(tmp_path):
    files = _files(tmp_path)
    write_changed(files)
    changed = tmp_path / "dashboards" / "nodes.json"
    files[changed] = '{"x": 1}\n'
    assert write_changed(files) == [changed]
    assert changed.read_text(encoding="utf-8") == files[changed]


def test_write_error_propagates(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(OSError):
        write_changed({blocker / "a.json": "[]\n"})
