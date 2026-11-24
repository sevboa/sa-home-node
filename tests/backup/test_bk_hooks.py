from dataclasses import replace

import pytest

from sa_home_node.backup import sealed
from sa_home_node.backup.hooks import backup_hooks
from sa_home_node.config import NodeConfig, NodeSection
from sa_home_node.plugins import Plugin
from sa_home_node.services.instances import InstanceStore
from tests.backup.test_bk_identity import Collector, make_spec


def make_config(tmp_path, **backup):
    node = NodeSection(id="node-a", state_path=str(tmp_path / "state" / "node.json"))
    return NodeConfig(path=tmp_path / "config.toml", node=node, sections={"backup": backup})


@pytest.fixture
def store(tmp_path):
    (tmp_path / "inst").mkdir()
    return InstanceStore(tmp_path / "inst", "node-a")


@pytest.fixture
def public():
    return sealed.dump_key(sealed.generate_keypair()[1])


def plugin(name="p", service="svc"):
    spec = replace(make_spec(Collector()), service=service)
    return Plugin(name=name, backup=spec)


def test_no_partner_is_empty(tmp_path, store, public):
    config = make_config(tmp_path, recipient_public_key=public)
    assert backup_hooks(config, [plugin()], store, {"svc"}) == []


def test_plugin_without_backup_is_skipped(tmp_path, store):
    config = make_config(tmp_path, partner="node-b")
    assert backup_hooks(config, [Plugin(name="x"), Plugin(name="y")], store, set()) == []


def test_partner_gives_passive_hook(tmp_path, store):
    config = make_config(tmp_path, partner="node-b")
    [hooks] = backup_hooks(config, [plugin()], store, {"svc"})
    assert hooks.passive("svc-id", "node-b")
    assert not hooks.passive("svc-id", "node-c")
    assert hooks.before_announce is None
    assert hooks.on_applied is not None


def test_publisher_needs_key_and_assignment(tmp_path, store, public):
    keyed = make_config(tmp_path, partner="node-b", recipient_public_key=public)
    [hooks] = backup_hooks(keyed, [plugin()], store, {"svc"})
    assert hooks.before_announce is not None
    [hooks] = backup_hooks(keyed, [plugin()], store, {"other"})
    assert hooks.before_announce is None
    [hooks] = backup_hooks(make_config(tmp_path, partner="node-b"), [plugin()], store, {"svc"})
    assert hooks.before_announce is None


def test_bad_key_leaves_passive_only(tmp_path, store):
    config = make_config(tmp_path, partner="node-b", recipient_public_key="junk")
    [hooks] = backup_hooks(config, [plugin()], store, {"svc"})
    assert hooks.before_announce is None
    assert hooks.passive is not None


def test_several_plugins(tmp_path, store, public):
    config = make_config(tmp_path, partner="node-b", recipient_public_key=public)
    plugins = [plugin("a", "one"), Plugin(name="mid"), plugin("b", "two")]
    first, second = backup_hooks(config, plugins, store, {"two"})
    assert first.before_announce is None
    assert second.before_announce is not None
