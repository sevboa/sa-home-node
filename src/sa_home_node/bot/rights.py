from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sa_home_node.bot.subs import WILDCARD


@dataclass(frozen=True)
class GuestRight:
    right: str
    title: str
    hint: str = ""


@dataclass(frozen=True)
class RightGroup:
    key: str
    title: str
    members: tuple[GuestRight, ...]
    fine: bool = False

    def rights(self) -> frozenset[str]:
        return frozenset(member.right for member in self.members)


class RightsCatalog:
    def __init__(self, groups: Sequence[RightGroup]) -> None:
        self.groups = tuple(groups)
        self._by_key = {group.key: group for group in self.groups}
        self._by_right: dict[str, tuple[RightGroup, GuestRight]] = {
            member.right: (group, member) for group in self.groups for member in group.members
        }

    def label(self, right: str) -> str:
        entry = self._by_right.get(right)
        if entry is None:
            return right
        group, member = entry
        return member.title if group.fine else f"{group.title}: {member.title}"

    def group(self, key: str) -> RightGroup | None:
        return self._by_key.get(key)

    def grantable(self) -> frozenset[str]:
        rights = frozenset().union(*(group.rights() for group in self.groups if not group.fine))
        return rights - {WILDCARD}

    def apply_group_on(self, current: frozenset[str], group: RightGroup) -> frozenset[str]:
        return current | group.rights()

    def apply_group_off(self, current: frozenset[str], group: RightGroup) -> frozenset[str]:
        return current - group.rights()
