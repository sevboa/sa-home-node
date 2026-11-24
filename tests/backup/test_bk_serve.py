from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from sa_home_node.backup import serve


class FakeStore:
    def __init__(self):
        self.items = {("a", "identity", "latest"): SimpleNamespace(blob=b"\x00ID", meta={"revision": 2})}

    def identity_versions(self, node):
        return [{"label": "latest", "meta": {"revision": 2}}]

    def snapshot_versions(self, node):
        return []

    def read_identity(self, node, label="latest"):
        return self.items.get((node, "identity", label))

    def read_snapshot(self, node, label="latest"):
        return self.items.get((node, "snapshot", label))


def test_action_specs():
    specs = {s.id: s for s in serve.action_specs()}
    assert specs[serve.ACTION_STORE_LIST].title == "💾 Список хранимых бэкапов"
    get = specs[serve.ACTION_STORE_GET]
    assert [(p.name, p.required, p.title) for p in get.params] == [
        ("node", True, "Чья копия"),
        ("kind", True, "Что"),
        ("label", False, "Версия"),
    ]
    assert get.params[1].choices == ("identity", "snapshot")


def test_list_and_get():
    store = FakeStore()
    assert serve.handle(store, "a", serve.ACTION_STORE_LIST, {"node": "a"}) == {
        "node": "a",
        "identity": [{"label": "latest", "meta": {"revision": 2}}],
        "snapshot": [],
    }
    reply = serve.handle(store, "a", serve.ACTION_STORE_GET, {"node": "a", "kind": "identity"})
    assert reply == {
        "kind": "identity",
        "label": "latest",
        "meta": {"revision": 2},
        "sealed": base64.b64encode(b"\x00ID").decode(),
    }


def test_unknown_action():
    assert serve.handle(FakeStore(), "a", "other", {}) is None


@pytest.mark.parametrize(
    ("partner", "args", "text"),
    [
        ("a", {"node": "b", "kind": "identity"}, "только напарника 'a', а не 'b'"),
        ("", {"node": "", "kind": "identity"}, "только напарника '', а не ''"),
        ("a", {"node": "a", "kind": "x"}, "неизвестный вид копии 'x'"),
        ("a", {"node": "a", "kind": "snapshot"}, "нет snapshot 'latest' для a"),
        ("a", {"node": "a", "kind": "identity", "label": "../x"}, "нет identity '../x' для a"),
    ],
)
def test_get_errors(partner, args, text):
    with pytest.raises(serve.ServeError, match=text):
        serve.handle(FakeStore(), partner, serve.ACTION_STORE_GET, args)


def test_list_foreign_node():
    with pytest.raises(serve.ServeError, match="только напарника 'a'"):
        serve.handle(FakeStore(), "a", serve.ACTION_STORE_LIST, {"node": "b"})
