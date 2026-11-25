from __future__ import annotations

import re
from collections.abc import Sequence

from aiogram import F, Router
from aiogram.types import Message

from sa_home_node.bot import nodeview, statusview
from sa_home_node.bot.middlewares import DENIED_TEXT, extract_command
from sa_home_node.bot.servicelink import ServiceLink
from sa_home_node.bot.subs import Subscription
from sa_home_node.proto.wire import node_address

_VALID_COMMAND = re.compile(r"^[a-z0-9_]+$")
_MAX_COMMAND_LEN = 32

NODE_PREFIX = "node_"
SVC_PREFIX = "svc_"
STATUS_RIGHT = "status"

NOT_FOUND_TEXT = "Не нашёл такую ноду или службу — рой мог измениться, см. /swarm."

router = Router(name="node_links")


def normalize(name: str) -> str | None:
    norm = name.lower().replace("-", "_")
    return norm if _VALID_COMMAND.fullmatch(norm) else None


def _command(prefix: str, *parts: str) -> str | None:
    norms = [normalize(p) for p in parts]
    if any(n is None for n in norms):
        return None
    name = prefix + "_".join(n for n in norms if n is not None)
    return f"/{name}" if len(name) <= _MAX_COMMAND_LEN else None


def node_command(node_id: str) -> str | None:
    return _command(NODE_PREFIX, node_id)


def svc_command(node_id: str, service: str) -> str | None:
    return _command(SVC_PREFIX, node_id, service)


def resolve_svc_candidates(arg: str, known_ids: Sequence[str]) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    for nid in known_ids:
        norm = normalize(nid)
        if norm is not None and arg.startswith(norm + "_"):
            candidates.append((nid, arg[len(norm) + 1 :]))
    candidates.sort(key=lambda c: len(c[1]))
    return candidates


def match_name(normalized: str, names: Sequence[str]) -> str | None:
    return next((name for name in names if normalize(name) == normalized), None)


async def _known_nodes(node_link: ServiceLink) -> tuple[str | None, list[str]]:
    try:
        state = await node_link.get_state()
    except statusview.SERVICE_CALL_ERRORS:
        return None, []
    own = state.get("node", "")
    peers = [p.get("id", "") for p in state.get("peers", [])]
    return own, [own, *[p for p in peers if p]]


async def _authorized_command(
    message: Message,
    node_link: ServiceLink,
    subscription: Subscription | None,
    required_command: str,
    prefix: str,
) -> tuple[str, str, list[str]] | None:
    command = extract_command(message.text)
    if command is None:
        return None
    if subscription is None or not subscription.allows_command(required_command):
        await message.answer(DENIED_TEXT)
        return None
    own, known = await _known_nodes(node_link)
    if own is None:
        await message.answer(nodeview.NODE_DOWN_TEXT)
        return None
    return command[len(prefix) :], own, known


@router.message(F.text.startswith(f"/{NODE_PREFIX}"))
async def cmd_node_card(
    message: Message,
    node_link: ServiceLink,
    subscription: Subscription | None = None,
) -> None:
    parsed = await _authorized_command(message, node_link, subscription, STATUS_RIGHT, NODE_PREFIX)
    if parsed is None:
        return
    arg, own, known = parsed
    target = match_name(arg, known)
    if target is None:
        await message.answer(NOT_FOUND_TEXT)
        return
    node_id = None if target == own else target
    text, keyboard = await nodeview.build_node_card_view(node_link, subscription, node_id)
    await message.answer(text, reply_markup=keyboard)


@router.message(F.text.startswith(f"/{SVC_PREFIX}"))
async def cmd_service_card(
    message: Message,
    node_link: ServiceLink,
    subscription: Subscription | None = None,
) -> None:
    parsed = await _authorized_command(message, node_link, subscription, nodeview.NODES_RIGHT, SVC_PREFIX)
    if parsed is None:
        return
    arg, own, known = parsed
    for target, tail in resolve_svc_candidates(arg, known):
        node_id = None if target == own else target
        try:
            state = await node_link.get_state(dst=node_address(node_id))
        except statusview.SERVICE_CALL_ERRORS:
            continue
        names = [s.get("name", "") for s in state.get("services", [])]
        service_name = match_name(tail, names)
        if service_name is None:
            continue
        text, keyboard = await nodeview.build_service_card_view(
            node_link, subscription, service_name, node_id
        )
        await message.answer(text, reply_markup=keyboard)
        return
    await message.answer(NOT_FOUND_TEXT)
