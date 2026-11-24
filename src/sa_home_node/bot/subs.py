from __future__ import annotations

from dataclasses import dataclass, replace

WILDCARD = "*"
SOURCE_CONFIG = "config"
SOURCE_GUEST = "guest"


@dataclass(frozen=True, slots=True)
class Subscription:
    name: str
    chat_id: int
    event_types: frozenset[str] = frozenset()
    except_event_types: frozenset[str] = frozenset()
    allowed_commands: frozenset[str] = frozenset()
    broken: bool = False
    source: str = SOURCE_CONFIG
    invited_by_chat_id: int = 0
    invited_at: str = ""
    invited_user: str = ""
    tags: frozenset[str] = frozenset()

    @property
    def is_guest(self) -> bool:
        return self.source == SOURCE_GUEST

    @property
    def is_owner(self) -> bool:
        return not self.broken and self.allows_command(WILDCARD)

    def accepts_event(self, event: str) -> bool:
        if self.broken:
            return False
        if event in self.except_event_types:
            return False
        if WILDCARD in self.event_types:
            return True
        return event in self.event_types

    def allows_command(self, command: str) -> bool:
        if self.broken:
            return False
        if WILDCARD in self.allowed_commands:
            return True
        return command in self.allowed_commands

    def allows_action(self, action_id: str, service: str) -> bool:
        if self.broken:
            return False
        if WILDCARD in self.allowed_commands:
            return True
        if f"*@{service}" in self.allowed_commands:
            return True
        if f"{action_id}@{service}" in self.allowed_commands:
            return True
        return action_id in self.allowed_commands

    def with_broken(self, broken: bool = True) -> Subscription:
        return replace(self, broken=broken)

    def with_allowed_commands(self, allowed_commands: frozenset[str]) -> Subscription:
        return replace(self, allowed_commands=allowed_commands)
