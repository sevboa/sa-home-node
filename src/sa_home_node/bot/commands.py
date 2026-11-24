from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sa_home_node.bot.subs import Subscription


@dataclass(frozen=True, slots=True)
class Command:
    name: str
    description: str
    universal: bool
    menu: bool = True
    right: str | None = None
    callback_codes: tuple[str, ...] = ()


CALLBACK_PREFIX = "st"
ACTION_CALLBACK_PREFIX = "act"
WAKE_CODE = "wake"
NODES_CODE = "nodes"
NODE_CARD_CODE = "nodecard"
SERVICE_CARD_CODE = "svc"
SWARM_NODES_CODE = "sw_nodes"
SWARM_NODE_CODE = "sw_node"
SWARM_PANEL_CODE = "sw_panel"
SWARM_SKILLS_CODE = "sw_skills"
SWARM_SKILL_CODE = "sw_skill"
SWARM_SVCS_CODE = "sw_svcs"
SWARM_SVC_CODE = "sw_svc"
SWARM_UPDATE_CODE = "sw_update"
DOWNTIME_PAGE_CODE = "downtime_page"
GUESTS_LIST_CODE = "g_list"
GUEST_CARD_CODE = "g_card"
GUEST_GROUP_ADD_CODE = "g_gon"
GUEST_GROUP_OFF_CODE = "g_goff"
GUEST_KICK_CONFIRM_CODE = "g_kick_ask"
GUEST_PERMS_CODE = "g_perms"
GUEST_PERM_ADD_CODE = "g_pon"
GUEST_PERM_ADD_LIST_CODE = "g_padd"
GUEST_PERM_OFF_CODE = "g_poff"
GUEST_REVOKE_CODE = "g_kick"
GUEST_STATS_CODE = "g_stats"
OPEN_CODES_LIST_CODE = "g_codes"
CODE_REVOKE_CODE = "g_code_off"

_CORE_COMMANDS: tuple[Command, ...] = (
    Command(
        name="ping",
        description="проверка живости (pong)",
        universal=True,
        menu=False,
        right=None,
    ),
    Command(
        name="swarm",
        description="сводка роя",
        universal=False,
        menu=False,
        right="nodes",
    ),
    Command(
        name="nodes",
        description="сводка роя (алиас /swarm)",
        universal=False,
        menu=False,
        right=None,
    ),
    Command(
        name="status",
        description="карточка локальной ноды",
        universal=False,
        menu=False,
        right=None,
    ),
    Command(
        name="status_full",
        description="подробный статус компонентов",
        universal=False,
        menu=False,
        right=None,
    ),
    Command(
        name="stats",
        description="статистика прогонов сканера",
        universal=False,
        menu=False,
        right=None,
    ),
    Command(
        name="scan_now",
        description="форс-скан датчиков и дисков",
        universal=False,
        menu=False,
        right=None,
    ),
    Command(
        name="downtime",
        description="последние отключения машины",
        universal=False,
        menu=False,
        right=None,
    ),
    Command(
        name="wake",
        description="разбудить домашний ПК (Wake-on-LAN)",
        universal=False,
        menu=False,
        right=None,
    ),
    Command(
        name="invite",
        description="выпустить код приглашения",
        universal=False,
        menu=False,
        right="invite",
    ),
    Command(
        name="guests",
        description="приглашённые и открытые коды",
        universal=False,
        menu=False,
        right="invite",
        callback_codes=(
            GUESTS_LIST_CODE,
            GUEST_CARD_CODE,
            GUEST_GROUP_ADD_CODE,
            GUEST_GROUP_OFF_CODE,
            GUEST_KICK_CONFIRM_CODE,
            GUEST_PERMS_CODE,
            GUEST_PERM_ADD_CODE,
            GUEST_PERM_ADD_LIST_CODE,
            GUEST_PERM_OFF_CODE,
            GUEST_REVOKE_CODE,
            GUEST_STATS_CODE,
            OPEN_CODES_LIST_CODE,
            CODE_REVOKE_CODE,
        ),
    ),
)

_CODE_MAP: dict[str, str] = {
    "full": "status_full",
    "stats": "stats",
    "downtime": "downtime",
    "downtime_page": "downtime",
    "nodes": "nodes",
    "nodecard": "status",
    "svc": "nodes",
    "wake": "wake",
    "sw_panel": "swarm",
    "sw_skills": "swarm",
    "sw_skill": "swarm",
    "sw_nodes": "swarm",
    "sw_node": "status",
    "sw_svcs": "nodes",
    "sw_svc": "nodes",
    "sw_update": "swarm",
}


class Registry:
    def __init__(self, plugin_commands: Iterable[Command] = ()) -> None:
        self._commands: dict[str, Command] = {c.name: c for c in _CORE_COMMANDS}
        for cmd in plugin_commands:
            self._commands[cmd.name] = cmd
        self._code_to_command: dict[str, str] = dict(_CODE_MAP)
        for cmd in self._commands.values():
            for code in cmd.callback_codes:
                self._code_to_command[code] = cmd.name

    def get(self, name: str) -> Command | None:
        return self._commands.get(name)

    def required_right(self, name: str) -> str:
        cmd = self._commands.get(name)
        return (cmd.right if cmd else None) or name

    def is_universal(self, name: str) -> bool:
        cmd = self._commands.get(name)
        return cmd is not None and cmd.universal

    def is_control(self, name: str) -> bool:
        cmd = self._commands.get(name)
        return cmd is not None and not cmd.universal

    def menu_control_commands_for(self, subscription: Subscription | None) -> list[Command]:
        if subscription is None:
            return []
        return [
            cmd
            for cmd in self._commands.values()
            if cmd.menu and not cmd.universal and subscription.allows_command(cmd.right or cmd.name)
        ]

    def can_run_action(self, subscription: Subscription | None, action: str, service: str) -> bool:
        if subscription is None:
            return False
        return subscription.allows_action(action, service)

    def command_for_callback(self, data: str | None) -> Command | None:
        if not data:
            return None
        if not data.startswith(f"{CALLBACK_PREFIX}:"):
            return None
        parts = data.split(":")
        if len(parts) < 2:
            return None
        code = parts[1]
        cmd_name = self._code_to_command.get(code)
        if cmd_name is None:
            return None
        return self._commands.get(cmd_name)


def panel_callback(code: str, *args: str | int) -> str:
    parts = [CALLBACK_PREFIX, code]
    parts.extend(str(a) for a in args if a is not None and a != "")
    return ":".join(parts)


def action_callback(
    action: str,
    value: str | None = None,
    node: str | None = None,
    service: str = "node",
) -> str:
    parts = [ACTION_CALLBACK_PREFIX, service, action]
    has_value = value is not None and value != ""
    has_node = node is not None and node != ""
    if has_value or has_node:
        parts.append(value if has_value else "")
    if has_node:
        parts.append(node)
    return ":".join(parts)


def wake_callback(node: str | None = None) -> str:
    if node is None or node == "":
        return f"{CALLBACK_PREFIX}:{WAKE_CODE}"
    return f"{CALLBACK_PREFIX}:{WAKE_CODE}:{node}"


def parse_wake_callback(data: str | None) -> str | None:
    if not data:
        return None
    prefix = f"{CALLBACK_PREFIX}:{WAKE_CODE}"
    if data == prefix:
        return None
    if data.startswith(f"{prefix}:"):
        node = data[len(prefix) + 1 :]
        return node if node else None
    return None


def parse_action_callback(data: str | None) -> tuple[str, str, str | None, str | None] | None:
    if not data:
        return None
    prefix = f"{ACTION_CALLBACK_PREFIX}:"
    if not data.startswith(prefix):
        return None
    parts = data[len(prefix) :].split(":")
    if len(parts) < 2:
        return None
    service, action = parts[0], parts[1]
    if not service or not action:
        return None
    value = parts[2] if len(parts) > 2 and parts[2] != "" else None
    node = parts[3] if len(parts) > 3 and parts[3] != "" else None
    return (service, action, value, node)
