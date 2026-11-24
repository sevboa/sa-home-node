from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aiogram import BaseMiddleware, Router

    from sa_home_node.bot.commands import Command
    from sa_home_node.bot.config import BotConfig
    from sa_home_node.bot.context import BotContext
    from sa_home_node.bot.rights import RightGroup
    from sa_home_node.bot.subs import Subscription
    from sa_home_node.proto.wire import Envelope


@dataclass(frozen=True)
class RouterSpec:
    order: int
    build: Callable[[BotContext], Router]


@dataclass(frozen=True)
class EventRoute:
    names: frozenset[str]
    handle: Callable[[BotContext, Envelope], Awaitable[None]]


@dataclass(frozen=True)
class BotSpec:
    commands: tuple[Command, ...] = ()
    routers: tuple[RouterSpec, ...] = ()
    middlewares: tuple[tuple[str, Callable[[BotContext], BaseMiddleware]], ...] = ()
    guest_rights: tuple[RightGroup, ...] = ()
    event_routes: tuple[EventRoute, ...] = ()
    schema: tuple[str, ...] = ()
    callback_guards: tuple[Callable[[Subscription, str, str], bool], ...] = ()
    help_lines: tuple[str, ...] = ()
    subscription_tags: Callable[[BotConfig], Mapping[int, frozenset[str]]] | None = None
    egress_candidates: Callable[..., Any] | None = None
    on_admitted: Callable[..., Awaitable[None]] | None = None
    on_start: Callable[[BotContext], Awaitable[None]] | None = None
    on_stop: Callable[[BotContext], Awaitable[None]] | None = None
    wake_guard: Callable[..., Any] | None = None
