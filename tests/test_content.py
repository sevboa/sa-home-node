from pathlib import Path

import pytest

from sa_home_node.content import ContentError, ContentStore


def make(tmp_path: Path, name: str, text: str) -> ContentStore:
    (tmp_path / f"{name}.toml").write_text(text)
    return ContentStore(tmp_path)


def test_get_and_contains(tmp_path: Path) -> None:
    store = make(tmp_path, "plug", 'name = "v"\ncount = 2\n')
    assert store.get("plug.name") == "v"
    assert store.get("plug.count") == 2
    assert store.get("plug.missing") is None
    assert store.get("plug.missing", 5) == 5
    assert "plug.name" in store
    assert "plug.missing" not in store


def test_missing_file(tmp_path: Path) -> None:
    store = ContentStore(tmp_path)
    with pytest.raises(ContentError, match="other.toml"):
        store.get("other.name")


@pytest.mark.parametrize("key", ["name", "plug.", ".name", ""])
def test_bad_key(tmp_path: Path, key: str) -> None:
    with pytest.raises(ContentError, match="<плагин>.<имя>"):
        ContentStore(tmp_path).get(key)


def test_require_missing(tmp_path: Path) -> None:
    store = make(tmp_path, "plug", 'a = 1\n')
    with pytest.raises(ContentError, match=r"plug\.b, plug\.c"):
        store.require(["plug.b", "plug.c"])


def test_require_ok(tmp_path: Path) -> None:
    store = make(tmp_path, "plug", 'a = 1\nb = 2\n')
    store.require(["plug.a", "plug.b"])


def test_bad_toml(tmp_path: Path) -> None:
    store = make(tmp_path, "plug", 'a = [1\n')
    with pytest.raises(ContentError, match="plug.toml"):
        store.get("plug.a")


def test_reads_once(tmp_path: Path) -> None:
    store = make(tmp_path, "plug", 'a = 1\n')
    assert store.get("plug.a") == 1
    (tmp_path / "plug.toml").write_text('a = 2\n')
    assert store.get("plug.a") == 1
