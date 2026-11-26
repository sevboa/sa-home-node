from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AsyncExitStack

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramConflictError
from aiogram.methods import GetMe

from sa_home_node.bot.alerts import AlertDispatcher, build_event_handler
from sa_home_node.bot.book import SubscriptionBook
from sa_home_node.bot.broadcast import broadcast_system
from sa_home_node.bot.commands import Registry
from sa_home_node.bot.config import BotServiceConfig, load_bot_config
from sa_home_node.bot.context import BotContext, OnAdmitted
from sa_home_node.bot.db import AppState, open_bot_db
from sa_home_node.bot.egress import DIRECT_PROBE_TIMEOUT_S
from sa_home_node.bot.egressmanager import EgressManager, SwitchNotices, pick_startup_route, startup_routes
from sa_home_node.bot.gate import Gatekeeper
from sa_home_node.bot.guestfile import GuestStore
from sa_home_node.bot.lifecycle import announce_shutdown, announce_startup, render_link_restored
from sa_home_node.bot.linkwatch import LinkWatchMiddleware
from sa_home_node.bot.menu import set_bot_commands
from sa_home_node.bot.monitor_parse import parse_outage
from sa_home_node.bot.nodeevents import build_node_event_handler
from sa_home_node.bot.notifier import Notifier
from sa_home_node.bot.outboxflush import OutboxFlusher
from sa_home_node.bot.retry import REQUEST_TIMEOUT_S, call_with_network_retry
from sa_home_node.bot.rights import RightsCatalog
from sa_home_node.bot.servicelink import ServiceLink
from sa_home_node.bot.setup import build_bot, build_dispatcher
from sa_home_node.bot.spec import BotSpec
from sa_home_node.bot.statusview import SERVICE_CALL_ERRORS
from sa_home_node.bot.store_events import EventStore
from sa_home_node.bot.store_invites import InviteStore
from sa_home_node.bot.store_outbox import OutboxStore
from sa_home_node.monitor.models import PowerEvent
from sa_home_node.plugins import ServiceContext
from sa_home_node.proto.client import EventCallback
from sa_home_node.proto.wire import Envelope
from sa_home_node.wake.ready import WakeGuard

log = logging.getLogger(__name__)

CandidateSource = Callable[[], Awaitable[list[dict]]]


class LateEvents:
    def __init__(self) -> None:
        self.handle: EventCallback | None = None

    async def __call__(self, env: Envelope) -> None:
        if self.handle is not None:
            await self.handle(env)


async def _probe_direct(bot: Bot) -> bool:
    session = AiohttpSession()
    try:
        await asyncio.wait_for(
            session.make_request(bot, GetMe(), timeout=int(DIRECT_PROBE_TIMEOUT_S)),
            timeout=DIRECT_PROBE_TIMEOUT_S + 2,
        )
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        with contextlib.suppress(Exception):
            await session.close()


def _merged_tags(specs: Sequence[BotSpec], config: object) -> dict[int, frozenset[str]]:
    tags: dict[int, frozenset[str]] = {}
    for spec in specs:
        if spec.subscription_tags is None:
            continue
        for chat_id, chat_tags in spec.subscription_tags(config).items():
            tags[chat_id] = tags.get(chat_id, frozenset()) | chat_tags
    return tags


def _chained_admitted(specs: Sequence[BotSpec]) -> OnAdmitted | None:
    hooks = [spec.on_admitted for spec in specs if spec.on_admitted is not None]
    if not hooks:
        return None

    async def call(*args: object) -> None:
        for hook in hooks:
            await hook(*args)

    return call


def _combined_guard(specs: Sequence[BotSpec]) -> WakeGuard | None:
    guards = [spec.wake_guard for spec in specs if spec.wake_guard is not None]
    if not guards:
        return None

    async def check(node_id: str) -> str | None:
        for guard in guards:
            denial = await guard(node_id)
            if denial is not None:
                return denial
        return None

    return check


def _candidate_source(specs: Sequence[BotSpec], ctx: BotContext) -> CandidateSource:
    sources = [spec.egress_candidates(ctx) for spec in specs if spec.egress_candidates is not None]

    async def fetch() -> list[dict]:
        found: list[dict] = []
        for source in sources:
            found.extend(await source())
        return found

    return fetch


async def _cancel(task: asyncio.Task[None]) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        log.warning("polling завершился с ошибкой при останове", exc_info=True)


async def _wait_started(polling: asyncio.Task[None], started: asyncio.Event) -> bool:
    waiter = asyncio.create_task(started.wait())
    try:
        await asyncio.wait({polling, waiter}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        waiter.cancel()
    return started.is_set()


async def run_bot(ctx: ServiceContext) -> bool:
    service = ctx.config
    if not isinstance(service, BotServiceConfig):
        raise TypeError("для оболочки бота нужен BotServiceConfig")
    if ctx.package_path is None:
        raise RuntimeError("для оболочки бота нужен пакет инстанса")
    specs = [plugin.bot for plugin in ctx.plugins if plugin.bot is not None]
    config = load_bot_config(service.config_path, ctx.package_path, service.guests_path)
    announced = False
    fenced = False
    async with AsyncExitStack() as stack:
        db = await open_bot_db(service.db_path, [sql for spec in specs for sql in spec.schema])
        stack.push_async_callback(db.close)
        app_state = AppState(db)
        events = EventStore(db)
        outbox = OutboxStore(db)

        bot = build_bot(config.telegram.token, config.telegram.proxy)
        stack.push_async_callback(bot.session.close)
        notifier = Notifier(bot, outbox=outbox)
        flusher = OutboxFlusher(notifier, outbox)
        auto_egress = config.telegram.proxy_mode == "auto"
        if auto_egress:
            await pick_startup_route(bot, startup_routes(config.telegram, service.egress_state_path))
        me = await call_with_network_retry(
            lambda: bot.get_me(request_timeout=REQUEST_TIMEOUT_S), what="get_me() при старте бота"
        )

        book = SubscriptionBook.from_config(
            config.subscriptions, config.guest_subscriptions, _merged_tags(specs, config)
        )

        async def on_reconnect(downtime: float) -> None:
            await broadcast_system(book, notifier, render_link_restored(downtime))

        bot.session.middleware(LinkWatchMiddleware(on_reconnect))
        guest_store = GuestStore(service.guests_path)
        gate = Gatekeeper(config.invites, InviteStore(db), book, guest_store, notifier)
        if not gate.enabled:
            log.info("Приглашения выключены")
        registry = Registry(command for spec in specs for command in spec.commands)
        await book.validate_on_startup(bot)

        async def say_goodbye() -> None:
            if announced:
                await announce_shutdown(book, notifier, app_state)

        stack.push_async_callback(say_goodbye)

        node_events = LateEvents()
        monitor_link = ServiceLink(
            service.monitor_socket,
            token=ctx.swarm_token,
            display_name="монитор",
            on_event=build_event_handler(AlertDispatcher(notifier, book, events)),
        )
        node_link = ServiceLink(
            ctx.node_socket, token=ctx.swarm_token, display_name="нода", on_event=node_events
        )

        async def refresh_menu() -> None:
            await set_bot_commands(bot, book, registry, await apps_link.actions())

        apps_link = ServiceLink(
            service.apps_socket, token=ctx.swarm_token, display_name="приложения", on_connected=refresh_menu
        )
        bot_ctx = BotContext(
            config=config,
            bot=bot,
            bot_username=me.username or "",
            book=book,
            notifier=notifier,
            events=events,
            app_state=app_state,
            db=db,
            node_link=node_link,
            monitor_link=monitor_link,
            apps_link=apps_link,
            registry=registry,
            catalog=RightsCatalog([group for spec in specs for group in spec.guest_rights]),
            guest_store=guest_store,
            gate=gate,
            help_lines=tuple(line for spec in specs for line in spec.help_lines),
            on_admitted=_chained_admitted(specs),
            wake_guard=_combined_guard(specs),
            content=ctx.content,
            sections=ctx.plugin_sections,
        )
        node_events.handle = build_node_event_handler(
            bot_ctx, tuple(route for spec in specs for route in spec.event_routes), config.about_name
        )
        for link in (monitor_link, node_link, apps_link):
            await link.start()
            stack.push_async_callback(link.stop)

        flusher.start()
        stack.push_async_callback(flusher.stop)
        if auto_egress:
            egress = EgressManager(
                bot.session,
                config.telegram,
                _candidate_source(specs, bot_ctx),
                lambda: _probe_direct(bot),
                on_switched=SwitchNotices(book, notifier, flusher).on_switched,
                state_path=service.egress_state_path,
                on_recovered=flusher.kick,
            )
            egress.start()
            stack.push_async_callback(egress.stop)

        dp = build_dispatcher(bot_ctx, ctx.plugins)
        await set_bot_commands(bot, book, registry, await apps_link.actions())

        async def stop_plugins() -> None:
            for spec in specs:
                if spec.on_stop is not None:
                    await spec.on_stop(bot_ctx)

        stack.push_async_callback(stop_plugins)
        for spec in specs:
            if spec.on_start is not None:
                await spec.on_start(bot_ctx)

        started = asyncio.Event()

        async def on_polling_started() -> None:
            started.set()

        dp.startup.register(on_polling_started)
        polling = asyncio.create_task(dp.start_polling(bot, handle_signals=False), name="polling")
        stack.push_async_callback(_cancel, polling)
        try:
            if await _wait_started(polling, started):
                log.info("Бот запущен")
                announced = True
                await announce_startup(book, notifier, app_state, _last_outage_reader(monitor_link))
            await polling
        except TelegramConflictError:
            fenced = True
            announced = False
            log.error("Этот же бот уже запущен где-то ещё (409 Conflict) — уступаю")
    return fenced


def _last_outage_reader(monitor_link: ServiceLink) -> Callable[[], Awaitable[PowerEvent | None]]:
    async def read() -> PowerEvent | None:
        try:
            state: Mapping[str, object] = await monitor_link.get_state()
        except SERVICE_CALL_ERRORS:
            return None
        return parse_outage(state.get("last_outage"))

    return read
