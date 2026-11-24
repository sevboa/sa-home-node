import json
from types import SimpleNamespace

import pytest

from sa_home_node.backup import identity, package, sealed
from sa_home_node.backup.config import BackupConfig
from sa_home_node.backup.hold import allow_publish
from sa_home_node.backup.identity import IdentityPublisher, identity_hooks
from sa_home_node.backup.spec import BackupSpec, IdentityError
from sa_home_node.backup.store import BackupStore
from sa_home_node.services.instances import InstanceMeta, InstanceStore

FORMAT = "svc.identity.v1"
SERVICE = "svc-id"


class Collector:
    def __init__(self, doc=None, error=None):
        self.doc = doc if doc is not None else {"k": "v"}
        self.error = error

    async def __call__(self, ctx):
        if self.error:
            raise IdentityError(self.error)
        return dict(self.doc)


def make_spec(collector):
    async def apply(ctx):
        return None

    return BackupSpec(
        service="svc",
        identity_service=SERVICE,
        identity_format=FORMAT,
        snapshot_format="svc.snapshot.v1",
        bundle_format="svc.bundle.v1",
        tables=("a",),
        skip_tables=frozenset(),
        primary_table="a",
        trigger_actions=frozenset(),
        collect_identity=collector,
        apply=apply,
        summarize=lambda bundle: [],
        rows_extra=lambda tables: {},
    )


@pytest.fixture
def keys():
    private, public = sealed.generate_keypair()
    return private, sealed.dump_key(public)


@pytest.fixture
def env(tmp_path, keys):
    private, public = keys
    state = tmp_path / "state" / "node-state.json"
    store = InstanceStore(tmp_path / "inst", "node-a")
    (tmp_path / "inst").mkdir()
    collector = Collector()
    backup = BackupConfig(recipient_public_key=public, partner="node-b")
    publisher = IdentityPublisher(make_spec(collector), backup, store, "node-a", state, SimpleNamespace())
    return SimpleNamespace(
        private=private, public=public, state=state, store=store, collector=collector,
        backup=backup, publisher=publisher, tmp=tmp_path,
    )


async def test_hold_blocks_and_warns_once(env, caplog):
    with caplog.at_level("WARNING"):
        assert not await env.publisher.publish_if_changed()
        assert not await env.publisher.publish_if_changed()
    assert [r.message for r in caplog.records].count(f"Бэкап identity: {identity.HOLD_HINT}") == 1
    assert not env.store.package_path(SERVICE, "node-a").exists()


async def test_publish_only_on_plaintext_change(env):
    allow_publish(env.state, "test")
    assert await env.publisher.publish_if_changed()
    pkg = env.store.package_path(SERVICE, "node-a")
    first = pkg.read_bytes()
    src = json.loads(pkg.with_name("svc-id.node-a.src.json").read_text())
    plain = package.canonical_bytes({"k": "v"})
    assert src == {"plain_hash": package.plain_hash(plain)}
    source, blob = package.parse_package(FORMAT, first)
    assert source == "node-a"
    assert sealed.open_sealed(env.private, blob) == plain
    assert not await env.publisher.publish_if_changed()
    assert pkg.read_bytes() == first
    env.collector.doc = {"k": "w"}
    assert await env.publisher.publish_if_changed()
    assert pkg.read_bytes() != first


async def test_identity_error_keeps_package(env, caplog):
    allow_publish(env.state, "test")
    assert await env.publisher.publish_if_changed()
    pkg = env.store.package_path(SERVICE, "node-a")
    before = pkg.read_bytes()
    env.collector.error = "нет ключа"
    with caplog.at_level("WARNING"):
        assert not await env.publisher.publish_if_changed()
    assert pkg.read_bytes() == before
    assert "нет ключа — пакет не обновлён" in caplog.text


def hooks_for(env, **over):
    backups = BackupStore(env.tmp / "backups")
    args = {"publishing": True}
    args.update(over)
    return backups, identity_hooks(
        make_spec(env.collector), env.backup, env.store, backups, "node-a", env.state, SimpleNamespace(), **args
    )


def test_no_partner_no_hooks(env):
    backup = BackupConfig(recipient_public_key=env.public)
    assert identity_hooks(
        make_spec(env.collector), backup, env.store, BackupStore(env.tmp / "b"), "node-a", env.state,
        SimpleNamespace(), publishing=True,
    ) is None


def test_passive_only_partner_identity(env):
    _, hooks = hooks_for(env)
    assert hooks.passive(SERVICE, "node-b")
    assert not hooks.passive(SERVICE, "node-c")
    assert not hooks.passive("other", "node-b")


def test_on_applied_saves_and_rejects_foreign_source(env):
    backups, hooks = hooks_for(env)
    meta = InstanceMeta(service=SERVICE, instance="node-b", rev=3, hash="h", updated_at="t", origin_node="node-b")
    data = package.render_package(FORMAT, "node-b", b"blob")
    hooks.on_applied(meta, data)
    assert backups.load_identity("node-b").blob == b"blob"
    with pytest.raises(IdentityError):
        hooks.on_applied(meta, package.render_package(FORMAT, "node-x", b"blob"))
    other = InstanceMeta(service=SERVICE, instance="node-c", rev=1, hash="h", updated_at="t", origin_node="node-c")
    hooks.on_applied(other, b"garbage")
    assert backups.load_identity("node-c") is None


def test_before_announce_conditions(env, caplog):
    assert hooks_for(env)[1].before_announce is not None
    assert hooks_for(env, publishing=False)[1].before_announce is None
    env.backup = BackupConfig(recipient_public_key="негоден", partner="node-b")
    with caplog.at_level("WARNING"):
        _, hooks = hooks_for(env)
    assert hooks.before_announce is None
    assert hooks.passive is not None
    assert "recipient_public_key негоден" in caplog.text
