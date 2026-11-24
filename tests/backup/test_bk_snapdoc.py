from __future__ import annotations

import json
import sqlite3
import zlib

import aiosqlite
import pytest

from sa_home_node.backup import sealed, snapdoc
from sa_home_node.backup.package import canonical_bytes

FMT = "test/snapshot/1"


@pytest.fixture
async def conn(tmp_path):
    async with aiosqlite.connect(tmp_path / "db.sqlite") as c:
        c.row_factory = sqlite3.Row
        await c.executescript(
            "CREATE TABLE tbl_a (id TEXT PRIMARY KEY, status TEXT, n INTEGER);"
            "CREATE TABLE tbl_b (x INTEGER, y TEXT);"
            "INSERT INTO tbl_a VALUES ('z','active',1),('a','off',2),('m','active',3);"
            "INSERT INTO tbl_b VALUES (2,'q'),(1,'w');"
        )
        yield c


async def test_dump_orders_by_pk_or_rowid(conn):
    out = await snapdoc.dump_tables(conn, ("tbl_a", "tbl_b", "missing"))
    assert list(out) == ["tbl_a", "tbl_b"]
    assert out["tbl_a"]["columns"] == ["id", "status", "n"]
    assert [r[0] for r in out["tbl_a"]["rows"]] == ["a", "m", "z"]
    assert out["tbl_b"]["rows"] == [[2, "q"], [1, "w"]]


async def test_dump_plain_cursor_rows(tmp_path):
    async with aiosqlite.connect(tmp_path / "p.sqlite") as c:
        await c.execute("CREATE TABLE tbl_a (id INTEGER PRIMARY KEY)")
        await c.execute("INSERT INTO tbl_a VALUES (3), (1)")
        out = await snapdoc.dump_tables(c, ("tbl_a",))
    assert out["tbl_a"]["rows"] == [[1], [3]]


def test_build_and_summarize():
    tables = {
        "tbl_a": {"columns": ["id", "status"], "rows": [["a", "active"], ["b", "off"]]},
        "tbl_b": {"columns": ["x"], "rows": []},
    }
    doc = snapdoc.build_document(FMT, "node-a", tables)
    assert doc == {"format": FMT, "node": "node-a", "tables": tables}

    def extra(t):
        idx = t["tbl_a"]["columns"].index("status")
        return {"tbl_a_active": sum(1 for r in t["tbl_a"]["rows"] if r[idx] == "active")}

    assert snapdoc.summarize(doc, "tbl_a", extra) == {"tbl_a": 2, "tbl_b": 0, "tbl_a_active": 1}
    assert snapdoc.summarize(doc, "tbl_a") == {"tbl_a": 2, "tbl_b": 0}
    assert snapdoc.summarize({"tables": {}}, "tbl_a") == {"tbl_a": 0}


def test_seal_open_round_trip():
    priv, pub = sealed.generate_keypair()
    doc = snapdoc.build_document(FMT, "node-a", {"tbl_a": {"columns": ["id"], "rows": [["я"]]}})
    blob = snapdoc.seal_document(pub, canonical_bytes(doc))
    assert snapdoc.open_snapshot(FMT, priv, blob) == doc
    assert json.loads(zlib.decompress(sealed.open_sealed(priv, blob))) == doc


def test_open_errors():
    priv, pub = sealed.generate_keypair()
    other, _ = sealed.generate_keypair()
    blob = snapdoc.seal_document(pub, canonical_bytes({"format": "other"}))
    with pytest.raises(snapdoc.SnapshotError, match="неизвестный формат"):
        snapdoc.open_snapshot(FMT, priv, blob)
    with pytest.raises(snapdoc.SnapshotError, match="не удалось расшифровать"):
        snapdoc.open_snapshot(FMT, other, blob)
    with pytest.raises(snapdoc.SnapshotError, match="не удалось расшифровать"):
        snapdoc.open_snapshot(FMT, priv, sealed.seal(pub, b"not zlib"))
    with pytest.raises(snapdoc.SnapshotError, match="не удалось расшифровать"):
        snapdoc.open_snapshot(FMT, priv, sealed.seal(pub, zlib.compress(b"{")))
