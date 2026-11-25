from __future__ import annotations

from collections.abc import Callable, Sequence

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from sa_home_node.bot import (
    handlers_basic,
    handlers_invites,
    handlers_node,
    handlers_status,
    handlers_wake,
    nodelinks,
    swarmpanel,
)
from sa_home_node.bot.context import BotContext
from sa_home_node.bot.middlewares import AuthorizationMiddleware, CallbackAuthorizationMiddleware, SilenceGate
from sa_home_node.bot.spec import RouterSpec
from sa_home_node.plugins import Plugin

EARLY_BELOW = 100
LATE_FROM = 900


def build_bot(token: str, proxy: str = "") -> Bot:
    session = AiohttpSession(proxy=proxy) if proxy else None
    return Bot(token, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def _plugin_routers(
    ctx: BotContext, specs: Sequence[RouterSpec], within: Callable[[int], bool]
) -> list[Router]:
    chosen = sorted((spec for spec in specs if within(spec.order)), key=lambda spec: spec.order)
    return [spec.build(ctx) for spec in chosen]


def build_dispatcher(ctx: BotContext, plugins: Sequence[Plugin]) -> Dispatcher:
    specs = [plugin.bot for plugin in plugins if plugin.bot is not None]
    routers = [spec for bot_spec in specs for spec in bot_spec.routers]
    dp = Dispatcher()
    dp.workflow_data.update(
        bot=ctx.bot,
        bot_config=ctx.config,
        wake_config=ctx.config.wake,
        about_name=ctx.config.about_name,
        bot_username=ctx.bot_username,
        book=ctx.book,
        notifier=ctx.notifier,
        gate=ctx.gate,
        guest_store=ctx.guest_store,
        catalog=ctx.catalog,
        registry=ctx.registry,
        guest_usage=ctx.guest_usage,
        on_admitted=ctx.on_admitted,
        wake_guard=ctx.wake_guard,
        help_lines=ctx.help_lines,
        node_link=ctx.node_link,
        monitor_link=ctx.monitor_link,
        apps_link=ctx.apps_link,
        app_state=ctx.app_state,
    )
    guards = tuple(guard for bot_spec in specs for guard in bot_spec.callback_guards)
    dp.update.outer_middleware(SilenceGate(ctx.book, ctx.gate))
    dp.message.middleware(AuthorizationMiddleware(ctx.book, ctx.registry))
    dp.callback_query.middleware(CallbackAuthorizationMiddleware(ctx.book, ctx.registry, guards))
    for bot_spec in specs:
        for name, factory in bot_spec.middlewares:
            getattr(dp, name).middleware(factory(ctx))
    ordered = [
        handlers_invites.router,
        *_plugin_routers(ctx, routers, lambda order: order < EARLY_BELOW),
        handlers_wake.router,
        swarmpanel.router,
        *_plugin_routers(ctx, routers, lambda order: EARLY_BELOW <= order < LATE_FROM),
        handlers_status.router,
        nodelinks.router,
        handlers_node.router,
        handlers_basic.router,
        *_plugin_routers(ctx, routers, lambda order: order >= LATE_FROM),
    ]
    for router in ordered:
        dp.include_router(router)
    return dp
