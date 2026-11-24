from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import stat
import sys
from pathlib import Path

import pytest
from support import TOKEN, free_port, wait_for

from sa_home_node import node
from sa_home_node.backup import cli as backup_cli
from sa_home_node.backup import package, sealed, snapdoc
from sa_home_node.backup.apply import apply_bundle
from sa_home_node.backup.bundle import load_bundle
from sa_home_node.backup.config import BackupConfig
from sa_home_node.backup.hold import HOLD_HINT, marker_path
from sa_home_node.backup.snapdoc import ACTION_GET_SNAPSHOT
from sa_home_node.backup.snapsync import build_backup
from sa_home_node.backup.spec import BackupSpec
from sa_home_node.backup.store import BackupStore, backups_dir
from sa_home_node.cli import main
from sa_home_node.config import load_config
from sa_home_node.monitor.db import Database
from sa_home_node.plugins import Plugin, ServiceContext, ServiceSpec
from sa_home_node.proto.client import ProtoClient
from sa_home_node.proto.describe import ServiceDescription, ServiceInfo
from sa_home_node.proto.wire import ERR_BAD_REQUEST, ERR_UNKNOWN_ACTION, Address, ProtoError
from sa_home_node.services.instances import InstanceStore
from sa_home_node.tasks.link import NodeLinkClient

pytestmark = [pytest.mark.skipif(sys.platform != "linux", reason="unix"), pytest.mark.timeout(60)]

FIXTURES = Path(__file__).parents[1] / "fixtures" / "backup"
SERVE = {case["name"]: case for case in json.loads((FIXTURES / "serve.json").read_text(encoding="utf-8"))}
PRIVATE, PUBLIC = sealed.generate_keypair()
IDENTITY_SERVICE = "svc-identity"
NODE_IDS = ("node-a", "node-b")


async def _stub_apply(ctx) -> None:
    APPLIED.append(ctx)


APPLIED: list = []


async def _collect(ctx) -> dict:
    return dict(LIVE[ctx.node_id].identity)


SPEC = BackupSpec(
    service="svc",
    identity_service=IDENTITY_SERVICE,
    identity_format="svc/identity/1",
    snapshot_format="svc/snapshot/1",
    bundle_format="svc/restore/1",
    tables=("tbl_a", "tbl_b"),
    skip_tables=frozenset({"tbl_b"}),
    primary_table="tbl_a",
    trigger_actions=frozenset(),
    collect_identity=_collect,
    apply=_stub_apply,
    summarize=lambda bundle: ["  свой: строка"],
    rows_extra=lambda tables: {"активных": 1},
)
LIVE: dict[str, LiveNode] = {}


class Handler:
    def __init__(self, ctx: ServiceContext) -> None:
        live = LIVE[ctx.node_id]
        self.node_id = ctx.node_id
        partner = live.partner
        self.link = NodeLinkClient(ctx.node_socket, ctx.swarm_token)

        async def ask(action: str, args: dict) -> dict:
            return await self.link.command(action, args, dst=Address(node=partner, service="svc"), timeout=5)

        live.backup = build_backup(SPEC, ctx.sections["backup"], live.state, lambda: live.db.conn, ctx.node_id, ask)

    def describe(self) -> ServiceDescription:
        backup = LIVE[self.node_id].backup
        return ServiceDescription(
            info=ServiceInfo(node=self.node_id, service="svc", version="1"), actions=tuple(backup.action_specs())
        )

    async def get_state(self) -> dict:
        return {}

    async def run_command(self, action: str, args: dict) -> dict:
        result = await LIVE[self.node_id].backup.handle(action, args)
        if result is None:
            raise ProtoError(ERR_UNKNOWN_ACTION, f"нет такого действия: {action}")
        return result


async def run(ctx: ServiceContext) -> None:
    backup = LIVE[ctx.node_id].backup
    await backup.start()
    try:
        await asyncio.Event().wait()
    finally:
        await backup.stop()


PLUGIN = Plugin(
    name="bk",
    services=(ServiceSpec("svc", run, handler=Handler),),
    sections=(("backup", BackupConfig),),
    backup=SPEC,
)


class LiveNode:
    def __init__(self, node_id: str, root: Path, sock_dir: str, port: int, peer_port: int) -> None:
        self.id = node_id
        self.root = root
        self.partner = NODE_IDS[1 - NODE_IDS.index(node_id)]
        self.sock = f"{sock_dir}/{node_id}.sock"
        self.state = root / "state.json"
        self.config_path = root / "config.toml"
        self.identity = {"format": SPEC.identity_format, "token": "k1"}
        self.db = Database(root / "svc.sqlite")
        self.backup = None
        self.stop = asyncio.Event()
        self.task: asyncio.Task | None = None
        key = root / "backup.key"
        sealed.write_private_key(key, PRIVATE)
        (root / "content").mkdir(parents=True)
        self.config_path.write_text(
            f'[node]\nid = "{node_id}"\nsocket = "{self.sock}"\nlisten = ["tcp://127.0.0.1:{port}"]\n'
            f'state_path = "{self.state}"\ndata_dir = "{root}/data"\ncontent_dir = "{root}/content"\n'
            f'assignments = ["svc"]\n'
            f'[backup]\nrecipient_public_key = "{sealed.dump_key(PUBLIC)}"\nprivate_key_file = "{key}"\n'
            f'partner = "{self.partner}"\nsnapshot_debounce_s = 0.2\nsnapshot_poll_s = 60\n'
            f'[swarm]\ntoken = "{TOKEN}"\nreplication_poll_interval_s = 0.3\n'
            f'[swarm.discovery]\nenabled = false\n'
            f'[[swarm.nodes]]\nid = "{self.partner}"\nendpoint = "tcp://127.0.0.1:{peer_port}"\n'
        )

    @property
    def store(self) -> BackupStore:
        return BackupStore(backups_dir(self.state), SPEC.primary_table)

    @property
    def package_path(self) -> Path:
        return InstanceStore(self.root / "instances", self.id).package_path(IDENTITY_SERVICE, self.id)

    async def seed(self) -> None:
        await self.db.open()
        for table in SPEC.tables:
            await self.db.conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, v TEXT)")
            await self.db.conn.executemany(f"INSERT INTO {table} VALUES (?, ?)", [(1, "a"), (2, "б")])
        await self.db.conn.commit()

    async def ask(self, target: str, action: str, args: dict | None = None) -> dict:
        client = ProtoClient(self.sock)
        await client.connect()
        try:
            return await client.command(action, args, dst=Address(node=target, service="svc"), timeout=10)
        finally:
            await client.close()

    async def ready(self) -> bool:
        async def up() -> bool:
            client = ProtoClient(self.sock)
            try:
                await client.connect()
                state = await client.get_state()
            except OSError:
                return False
            finally:
                await client.close()
            return any(p["alive"] for p in state.get("peers", []))

        deadline = asyncio.get_running_loop().time() + 20
        while asyncio.get_running_loop().time() < deadline:
            if await up():
                return True
            await asyncio.sleep(0.1)
        return False

    async def shutdown(self) -> None:
        self.stop.set()
        if self.task is not None:
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(self.task, timeout=30)
        await self.db.close()


@pytest.fixture
async def pair(tmp_path, sock_dir, monkeypatch):
    monkeypatch.setattr(node, "load_plugins", lambda names: [PLUGIN])
    monkeypatch.setattr(backup_cli, "load_plugins", lambda names: [PLUGIN])
    monkeypatch.setattr(node, "arm_hard_exit", lambda total: None)
    ports = [free_port(), free_port()]
    nodes = [
        LiveNode(node_id, tmp_path / node_id, sock_dir, ports[i], ports[1 - i]) for i, node_id in enumerate(NODE_IDS)
    ]
    LIVE.update({n.id: n for n in nodes})
    APPLIED.clear()
    for n in nodes:
        await n.seed()
        n.task = asyncio.create_task(node.run_node(n.config_path, stop=n.stop))
    assert all([await n.ready() for n in nodes])
    yield nodes
    await asyncio.gather(*(n.shutdown() for n in nodes), return_exceptions=True)
    LIVE.clear()


@pytest.fixture
async def released(pair):
    source, holder = pair
    assert main_backup(source, "release") == 0
    assert await wait_for(lambda: holder.store.load_identity("node-a") is not None, 20)
    source.backup.touch()
    assert await wait_for(lambda: holder.store.load_snapshot("node-a") is not None, 20)
    return source, holder


def main_backup(live: LiveNode, *argv: str) -> int:
    return main(["backup", "--config", str(live.config_path), *argv])


def identity_rev(holder: LiveNode) -> int:
    stored = holder.store.load_identity("node-a")
    return stored.meta["rev"] if stored else 0


async def test_hold_until_release_then_rev_grows_only_on_change(pair) -> None:
    source, holder = pair
    await asyncio.sleep(1.5)
    assert not source.package_path.exists()
    assert holder.store.load_identity("node-a") is None
    with pytest.raises(ProtoError) as held:
        await holder.ask("node-a", ACTION_GET_SNAPSHOT)
    assert (held.value.code, held.value.message) == (ERR_BAD_REQUEST, HOLD_HINT)

    assert main_backup(source, "release") == 0
    assert marker_path(source.state).exists()
    assert await wait_for(lambda: identity_rev(holder) == 1, 20)
    opened = package.open_identity(PRIVATE, holder.store.load_identity("node-a").blob)
    assert opened == source.identity
    await asyncio.sleep(1.2)
    assert identity_rev(holder) == 1

    source.identity["token"] = "k2"
    assert await wait_for(lambda: identity_rev(holder) == 2, 20)
    assert package.open_identity(PRIVATE, holder.store.load_identity("node-a").blob)["token"] == "k2"


async def test_snapshot_get_and_poke(released) -> None:
    source, holder = released
    first = holder.store.load_snapshot("node-a")
    doc = snapdoc.open_snapshot(SPEC.snapshot_format, PRIVATE, first.blob)
    assert doc["node"] == "node-a"
    assert doc["tables"]["tbl_a"]["rows"] == [[1, "a"], [2, "б"]]
    assert first.meta["rows"] == {"tbl_a": 2, "tbl_b": 2, "активных": 1}

    got = await holder.ask("node-a", ACTION_GET_SNAPSHOT, {})
    assert got["hash"] == first.meta["hash"] and got["meta"]["source"] == "node-a"
    assert await holder.ask("node-a", ACTION_GET_SNAPSHOT, {"have_hash": got["hash"]}) == {
        "unchanged": True,
        "hash": got["hash"],
    }

    await source.db.conn.execute("INSERT INTO tbl_a VALUES (3, 'в')")
    await source.db.conn.commit()
    source.backup.touch()
    assert await wait_for(lambda: holder.store.load_snapshot("node-a").meta["hash"] != first.meta["hash"], 20)
    assert holder.store.load_snapshot("node-a").meta["rows"]["tbl_a"] == 3
    assert holder.store.load_snapshot("node-a").meta["rev"] == 2


async def test_store_serves_only_partner_copies(released) -> None:
    source, holder = released
    listing = await source.ask("node-a", "backup_store_list", {"node": "node-b"})
    assert listing == {"node": "node-b", "identity": [], "snapshot": []}
    listing = await holder.ask("node-b", "backup_store_list", {"node": "node-a"})
    assert [v["label"] for v in listing["identity"]] == ["latest"]
    assert [v["label"] for v in listing["snapshot"]] == ["latest", "last_nonempty"]
    item = await holder.ask("node-b", "backup_store_get", {"node": "node-a", "kind": "identity"})
    assert package.open_identity(PRIVATE, base64.b64decode(item["sealed"])) == source.identity
    for action, args, case in (
        ("backup_store_list", {"node": "node-c"}, "list_foreign_node"),
        ("backup_store_get", {"node": "node-c", "kind": "identity"}, "get_foreign_node"),
        ("backup_store_get", {"node": "node-a", "kind": "other"}, "get_unknown_kind"),
        ("backup_store_get", {"node": "node-a", "kind": "identity", "label": "nope"}, "get_identity_unknown_label"),
    ):
        with pytest.raises(ProtoError) as caught:
            await holder.ask("node-b", action, args)
        expected = SERVE[case]["error"]["message"].replace("node-b", "node-a")
        assert (caught.value.code, caught.value.message) == (ERR_BAD_REQUEST, expected)


async def dry_run(source: LiveNode, target: Path) -> int:
    return await asyncio.to_thread(
        main_backup, source, "restore", "node-a", "--dry-run", str(target), "--holder", "node-b"
    )


async def test_restore_dry_run_builds_bundle_directory(released, tmp_path) -> None:
    source, _ = released
    target = tmp_path / "bundle"
    assert await dry_run(source, target) == 0
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    assert sorted(p.name for p in target.iterdir()) == ["identity.json", "meta.json", "snapshot.json", "summary.txt"]
    assert json.loads((target / "identity.json").read_text(encoding="utf-8")) == source.identity
    snapshot = json.loads((target / "snapshot.json").read_text(encoding="utf-8"))
    assert snapshot["tables"]["tbl_a"]["rows"] == [[1, "a"], [2, "б"]]
    meta = json.loads((target / "meta.json").read_text(encoding="utf-8"))
    assert (meta["format"], meta["node"], meta["snapshot_label"]) == (SPEC.bundle_format, "node-a", "latest")


async def test_apply_bundle_runs_plugin_step_and_sets_marker(released, tmp_path) -> None:
    source, _ = released
    target = tmp_path / "bundle"
    assert await dry_run(source, target) == 0
    marker_path(source.state).unlink()
    lines: list[str] = []
    config = load_config(source.config_path)
    await apply_bundle(
        SPEC,
        load_bundle(target, SPEC.bundle_format),
        config,
        config_path=source.config_path,
        io=object(),
        log=lines.append,
        wipe_db=False,
    )
    (ctx,) = APPLIED
    assert ctx.bundle["identity"] == source.identity and ctx.bundle["node"] == "node-a"
    assert marker_path(source.state).exists()
    assert lines and "маркер публикации бэкапа" in lines[-1]
