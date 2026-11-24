from collections.abc import Collection, Sequence

from sa_home_node.backup.config import BackupConfig
from sa_home_node.backup.identity import identity_hooks
from sa_home_node.backup.store import BackupStore, backups_dir
from sa_home_node.config import NodeConfig
from sa_home_node.plugins import Plugin, ReplicationHooks
from sa_home_node.services.instances import InstanceStore


def backup_hooks(
    config: NodeConfig,
    plugins: Sequence[Plugin],
    store: InstanceStore,
    assigned: Collection[str],
) -> list[ReplicationHooks]:
    result: list[ReplicationHooks] = []
    for plugin in plugins:
        spec = plugin.backup
        if spec is None:
            continue
        backup = config.section("backup", BackupConfig)
        hooks = identity_hooks(
            spec,
            backup,
            store,
            BackupStore(backups_dir(config.state_file), spec.primary_table),
            config.node.id,
            config.state_file,
            config,
            publishing=spec.service in assigned and bool(backup.recipient_public_key.strip()),
        )
        if hooks is not None:
            result.append(hooks)
    return result
