import dataclasses
import inspect
from pathlib import Path

import pytest
from pydantic import ValidationError

from sa_home_node.backup.config import BackupConfig
from sa_home_node.backup.spec import ApplyContext, BackupContext, BackupSpec, IdentityError, PrivilegedIO


async def _identity(ctx):
    return {}


async def _apply(ctx):
    return None


def _spec(**over):
    values = {
        "service": "svc",
        "identity_service": "svc-id",
        "identity_format": "svc.identity.v1",
        "snapshot_format": "svc.snapshot.v1",
        "bundle_format": "svc.bundle.v1",
        "tables": ("a", "b"),
        "skip_tables": frozenset({"c"}),
        "primary_table": "a",
        "trigger_actions": frozenset({"add"}),
        "collect_identity": _identity,
        "apply": _apply,
        "summarize": lambda bundle: [],
        "rows_extra": lambda tables: {},
    }
    values.update(over)
    return BackupSpec(**values)


def test_config_defaults():
    cfg = BackupConfig()
    assert cfg.recipient_public_key == ""
    assert cfg.private_key_file == ""
    assert cfg.partner == ""
    assert cfg.snapshot_interval_s == 600.0
    assert cfg.snapshot_debounce_s == 15.0
    assert cfg.snapshot_poll_s == 300.0


def test_config_extra_key_passes():
    cfg = BackupConfig(partner="p", xray_config="/x.json")
    assert cfg.partner == "p"
    assert cfg.xray_config == "/x.json"
    assert cfg.model_extra == {"xray_config": "/x.json"}


def test_config_rejects_bad_type():
    with pytest.raises(ValidationError):
        BackupConfig(snapshot_interval_s="часто")


def test_spec_frozen():
    spec = _spec()
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.service = "other"


def test_spec_required_fields():
    for field in dataclasses.fields(BackupSpec):
        assert field.default is dataclasses.MISSING
        assert field.default_factory is dataclasses.MISSING
    with pytest.raises(TypeError):
        BackupSpec(service="svc")


@pytest.mark.parametrize("cls", [BackupContext, ApplyContext])
def test_contexts_frozen(cls):
    assert cls.__dataclass_params__.frozen
    assert [f.name for f in dataclasses.fields(cls)] == (
        ["config", "backup", "node_id"]
        if cls is BackupContext
        else ["bundle", "config", "config_path", "io", "log", "wipe_db"]
    )


async def test_hooks_are_awaited():
    spec = _spec()
    ctx = BackupContext(config=None, backup=BackupConfig(), node_id="n")
    assert await spec.collect_identity(ctx) == {}
    assert await spec.apply(ApplyContext({}, None, Path("c.toml"), None, print, False)) is None


def test_privileged_io_protocol():
    names = sorted(n for n in vars(PrivilegedIO) if not n.startswith("_"))
    assert names == ["read_root", "restart", "unit_active", "write_root"]
    params = inspect.signature(PrivilegedIO.unit_active).parameters
    assert params["user"].kind is inspect.Parameter.KEYWORD_ONLY


def test_identity_error():
    assert issubclass(IdentityError, Exception)
