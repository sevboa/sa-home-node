from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import aiosqlite
import pytest

from sa_home_node.backup.apply import ApplyError, apply_bundle, check_bundle, count_peers, import_snapshot
from sa_home_node.backup.spec import ApplyContext, BackupSpec
from sa_home_node.backup.tomlpatch import patch_toml_sections
from sa_home_node.config import NodeConfig, NodeSection

FIXTURE = json.loads((Path(__file__).parents[1] / "fixtures" / "backup" / "apply.json").read_text(encoding="utf-8"))
SECTIONS = {s["name"]: s for s in FIXTURE}
TABLES = ("tbl_a", "tbl_b")
SKIP = frozenset({"tbl_b"})
DB_LABEL = "/state/svc.db"
ORIGINAL = (
    '[node]\nid = "node-a"\n\n[svc]\nsocket = "/run/local.sock"\ndb_path = "/state/local.db"\n'
    'mtu = 1280\n\n[svc.sub]\ninbound_tag = "old"\n\n[other]\nx = 1\n'
)
IDENTITY = {
    "format": "svc/identity/1",
    "config": {
        "svc": {
            "endpoint_host": "host-a.example",
            "mtu": 1380,
            "socket": "/remote.sock",
            "side": {"interface": "if1", "port": 51821},
            "sub": {"inbound_tag": "in-a", "server_public_key": "pub-a", "deep": {"flag": True}},
        }
    },
}
SNAPSHOT = {
    "tables": {
        "tbl_a": {"columns": ["id", "name", "status"], "rows": [[1, "a", "active"], [2, "b", "revoked"]]},
        "tbl_b": {"columns": ["k", "n"], "rows": [["z", 5]]},
    }
}
OLD_ROWS = [(2, "old", "active"), (9, "keep", "active")]
CASE_SETUP = {
    "happy_path": {},
    "existing_rows_refused": {"rows": OLD_ROWS},
    "existing_rows_wipe_db": {"rows": OLD_ROWS},
    "without_snapshot_keeps_rows": {"rows": OLD_ROWS, "snapshot": None},
    "empty_config_text": {"config": ""},
    "config_missing": {"config": None},
    "other_node": {"node": "node-c"},
    "snapshot_skips_table": {},
}


async def noop(*_):
    return {}


class IO:
    def __getattr__(self, name):
        raise AssertionError(name)


def make_spec(apply_step=noop):
    return BackupSpec(
        service="svc",
        identity_service="svc-identity",
        identity_format="svc/identity/1",
        snapshot_format="svc/snapshot/1",
        bundle_format="svc/restore/1",
        tables=TABLES,
        skip_tables=SKIP,
        primary_table="tbl_a",
        trigger_actions=frozenset(),
        collect_identity=noop,
        apply=apply_step,
        summarize=lambda bundle: [],
        rows_extra=lambda tables: {},
    )


async def make_db(path, rows, b_rows=()):
    async with aiosqlite.connect(path) as conn:
        await conn.execute("CREATE TABLE tbl_a (id INTEGER PRIMARY KEY, name TEXT, status TEXT)")
        await conn.execute("CREATE TABLE tbl_b (k TEXT PRIMARY KEY, n INTEGER)")
        await conn.executemany("INSERT INTO tbl_a VALUES (?, ?, ?)", rows)
        await conn.executemany("INSERT INTO tbl_b VALUES (?, ?)", b_rows)
        await conn.commit()


def dump(path, table):
    with sqlite3.connect(path) as conn:
        try:
            return [list(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]
        except sqlite3.OperationalError:
            return None


@pytest.mark.parametrize("case", SECTIONS["check_bundle"]["cases"], ids=lambda c: c["name"])
def test_check_bundle(case):
    bundles = {
        "ok": {"node": "node-a", "identity": {"format": "svc/identity/1"}},
        "other_node": {"node": "node-c", "identity": {"format": "svc/identity/1"}},
        "no_identity": {"node": "node-a", "identity": {}},
        "no_identity_key": {"node": "node-a"},
        "identity_without_format": {"node": "node-a", "identity": {"x": 1}},
    }
    if "error" in case:
        with pytest.raises(ApplyError) as exc:
            check_bundle(make_spec(), bundles[case["name"]], "node-a")
        assert str(exc.value) == case["error"]["message"]
    else:
        assert check_bundle(make_spec(), bundles[case["name"]], "node-a") is None


IMPORT_SNAPSHOTS = {
    "merge": {"tbl_a": (["id", "name", "status"], [[1, "a", "active"], [2, "b2", "revoked"], [7, "g", "active"]])},
    "wipe": {"tbl_a": (["id", "name", "status"], [[1, "a", "active"], [2, "b2", "revoked"], [7, "g", "active"]])},
    "db_without_table_a": {"tbl_a": (["id", "name", "status"], [[1, "a", "active"]])},
    "snapshot_without_table_a": {},
    "snapshot_only_skipped_table": {"tbl_b": (["k", "n"], [["q", 1]])},
    "no_tables_key": None,
    "column_order_and_subset": {"tbl_a": (["status", "id", "extra"], [["active", 5, "x"], ["revoked", 6, "y"]])},
    "empty_rows_wipe": {"tbl_a": (["id", "name", "status"], [])},
    "ids_preserved": {"tbl_a": (["id", "name", "status"], [[41, "x", "active"], [42, "y", "active"]])},
}


@pytest.mark.parametrize("case", SECTIONS["import_snapshot"]["cases"], ids=lambda c: c["name"])
async def test_import_snapshot(case, tmp_path):
    path = tmp_path / "db.sqlite"
    await make_db(path, OLD_ROWS, [("k9", 1)])
    if case["name"] == "db_without_table_a":
        with sqlite3.connect(path) as conn:
            conn.execute("DROP TABLE tbl_a")
    spec = IMPORT_SNAPSHOTS[case["name"]]
    snapshot = {} if spec is None else {"tables": {t: {"columns": c, "rows": r} for t, (c, r) in spec.items()}}
    async with aiosqlite.connect(path) as conn:
        done = await import_snapshot(conn, snapshot, SECTIONS["import_snapshot"]["tables"], frozenset(SECTIONS["import_snapshot"]["skip_tables"]), wipe=case["wipe"])
        await conn.commit()
    assert done == case["result"]
    assert dump(path, "tbl_a") == (None if case["tbl_a"] is None else [list(r) for r in case["tbl_a"]])
    assert dump(path, "tbl_b") == [list(r) for r in case["tbl_b"]]


async def test_count_peers(tmp_path):
    path = tmp_path / "db.sqlite"
    async with aiosqlite.connect(path) as conn:
        assert await count_peers(conn, "tbl_a") == 0
    await make_db(path, OLD_ROWS)
    async with aiosqlite.connect(path) as conn:
        assert await count_peers(conn, "tbl_a") == 2


def make_step(root, db_path, snapshot):
    async def step(ctx: ApplyContext):
        text = ctx.config_path.read_text(encoding="utf-8")
        async with aiosqlite.connect(db_path) as conn:
            have = await count_peers(conn, "tbl_a")
            if snapshot is not None and have and not ctx.wipe_db:
                raise ApplyError(f"в {DB_LABEL} уже {have} пиров — не затираю. Если это пересобранная нода с тестовыми данными, повторите с --wipe-db")
            new = patch_toml_sections(text, "svc", ctx.bundle["identity"]["config"]["svc"], keep=("socket", "db_path"))
            if new != text:
                shutil.copy2(ctx.config_path, ctx.config_path.with_name(ctx.config_path.name + ".pre-restore"))
                ctx.config_path.write_text(new, encoding="utf-8")
                ctx.log(f"config: [svc]/[svc.sub] восстановлены в {ctx.config_path}")
            if snapshot is not None:
                done = await import_snapshot(conn, snapshot, TABLES, SKIP, wipe=ctx.wipe_db)
                await conn.commit()
                ctx.log("БД: " + ", ".join(f"{t}={n}" for t, n in done.items()))

    return step


def make_config(root):
    return NodeConfig(path=root / "config.toml", node=NodeSection(id="node-a", state_path=str(root / "state" / "node.json")), sections={})


@pytest.mark.parametrize("case", SECTIONS["apply_flow"]["cases"], ids=lambda c: c["name"])
async def test_apply_flow(case, tmp_path):
    root = tmp_path / "apply"
    root.mkdir()
    setup = CASE_SETUP[case["name"]]
    config = make_config(root)
    config_text = setup.get("config", ORIGINAL)
    if config_text is not None:
        config.path.write_text(config_text, encoding="utf-8")
    db_path = root / "svc.db"
    await make_db(db_path, setup.get("rows", []), [("k9", 1)] if setup.get("rows") else [])
    snapshot = setup.get("snapshot", SNAPSHOT)
    bundle = {"node": setup.get("node", "node-a"), "identity_label": "latest", "identity": IDENTITY, "snapshot": snapshot}
    lines = []
    spec = make_spec(make_step(root, db_path, snapshot))
    marker = root / "state" / "backup-publish.ok"
    if "error" in case:
        with pytest.raises(ApplyError) as exc:
            await apply_bundle(spec, bundle, config, config_path=config.path, io=IO(), log=lines.append, wipe_db=case["wipe_db"])
        assert str(exc.value).replace(str(root.parent), "<root>") == case["error"]["message"]
    else:
        await apply_bundle(spec, bundle, config, config_path=config.path, io=IO(), log=lines.append, wipe_db=case["wipe_db"])
    assert [ln.replace(str(root.parent), "<root>") for ln in lines] == case["log"]
    assert (config.path.read_text(encoding="utf-8") if config.path.exists() else None) == case["config"]
    pre = config.path.with_name("config.toml.pre-restore")
    assert (pre.read_text(encoding="utf-8") if pre.exists() else None) == case["pre_restore"]
    if case["marker"] is None:
        assert not marker.exists()
    else:
        assert marker.read_text(encoding="utf-8").split(" ", 1)[1] == case["marker"]["text"].split(" ", 1)[1]
        assert oct(marker.stat().st_mode & 0o777) == case["marker"]["mode"]
    assert dump(db_path, "tbl_a") == [list(r) for r in case["tbl_a"]]
    assert dump(db_path, "tbl_b") == [list(r) for r in case["tbl_b"]]


async def test_apply_idempotent(tmp_path):
    root = tmp_path / "apply"
    root.mkdir()
    config = make_config(root)
    config.path.write_text(ORIGINAL, encoding="utf-8")
    db_path = root / "svc.db"
    await make_db(db_path, [])
    bundle = {"node": "node-a", "identity_label": "latest", "identity": IDENTITY, "snapshot": None}
    spec = make_spec(make_step(root, db_path, None))
    expected = SECTIONS["apply_idempotent"]
    runs = []
    for _ in range(2):
        lines = []
        await apply_bundle(spec, bundle, config, config_path=config.path, io=IO(), log=lines.append)
        runs.append([ln.replace(str(root.parent), "<root>") for ln in lines])
        pre = config.path.with_name("config.toml.pre-restore")
        if len(runs) == 1:
            first = config.path.read_text(encoding="utf-8")
            pre.unlink()
    assert runs == [expected["first_log"], expected["second_log"]]
    assert not pre.exists()
    assert config.path.read_text(encoding="utf-8") == first
