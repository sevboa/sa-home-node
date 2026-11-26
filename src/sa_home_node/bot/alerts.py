from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sa_home_node.bot.book import SubscriptionBook
from sa_home_node.bot.broadcast import broadcast
from sa_home_node.bot.monitor_parse import (
    parse_host_event,
    parse_overheat_event,
    parse_smart_change,
)
from sa_home_node.bot.notifier import Notifier
from sa_home_node.bot.render_alerts import render_event, render_host_event, render_smart_change
from sa_home_node.bot.store_events import NOTIF_ALERT, NOTIF_CLEARED, EventStore
from sa_home_node.bot.store_outbox import OUTBOX_KIND_ALERT
from sa_home_node.bot.subs import Subscription
from sa_home_node.monitor.hostmodels import EVENT_HOST_DEGRADED, EVENT_HOST_RECOVERED, HostEvent
from sa_home_node.monitor.models import (
    EVENT_OVERHEAT_CLEARED,
    EVENT_OVERHEAT_STARTED,
    EVENT_SMART_DEGRADED,
    EVENT_SMART_RECOVERED,
    DispatchResult,
    Event,
    SmartChange,
)
from sa_home_node.proto.wire import Envelope

log = logging.getLogger(__name__)


class AlertDispatcher:
    def __init__(
        self,
        notifier: Notifier,
        book: SubscriptionBook,
        events: EventStore,
    ) -> None:
        self._notifier = notifier
        self._book = book
        self._events = events

    async def dispatch_alert(self, event: Event) -> DispatchResult:
        text = render_event(event)
        now = datetime.now(tz=UTC)
        delivered = False
        for sub in self._book.accepting(event.type):
            message_id = await self._notifier.send_direct(
                sub.chat_id,
                text,
                outbox_kind=OUTBOX_KIND_ALERT,
                dedup_key=f"health:{event.component_id}",
            )
            if message_id is not None:
                delivered = True
                await self._events.record_notification(
                    event.component_id, sub.chat_id, NOTIF_ALERT, message_id, now
                )
        return DispatchResult(delivered=delivered, handled=True)

    async def dispatch_clear(self, event: Event) -> DispatchResult:
        text = render_event(event)
        now = datetime.now(tz=UTC)
        delivered = False
        for sub in self._book.accepting(event.type):
            reply_to = await self._events.get_alert_message_id(event.component_id, sub.chat_id)
            message_id = await self._notifier.send_direct(
                sub.chat_id,
                text,
                reply_to_message_id=reply_to,
                outbox_kind=OUTBOX_KIND_ALERT,
                dedup_key=f"health:{event.component_id}",
            )
            if message_id is not None:
                delivered = True
                await self._events.record_notification(
                    event.component_id, sub.chat_id, NOTIF_CLEARED, message_id, now
                )
        return DispatchResult(delivered=delivered, handled=True)

    async def _fanout_without_outbox(self, subs: list[Subscription], text: str) -> DispatchResult:
        delivered = await broadcast(subs, self._notifier, text) > 0
        return DispatchResult(delivered=delivered, handled=delivered or not subs)

    async def _dispatch_host(self, event: HostEvent) -> DispatchResult:
        subs = self._book.accepting(event.type)
        return await self._fanout_without_outbox(subs, render_host_event(event))

    async def dispatch_host_alert(self, event: HostEvent) -> DispatchResult:
        return await self._dispatch_host(event)

    async def dispatch_host_clear(self, event: HostEvent) -> DispatchResult:
        return await self._dispatch_host(event)

    async def dispatch_smart(self, change: SmartChange) -> DispatchResult:
        subs = self._book.accepting(change.event_type)
        return await self._fanout_without_outbox(subs, render_smart_change(change))


def build_event_handler(dispatcher: AlertDispatcher) -> Callable[[Envelope], Awaitable[None]]:
    async def handle(env: Envelope) -> None:
        name = env.payload.get("event")
        data = env.payload.get("data", {})
        try:
            if name == EVENT_OVERHEAT_STARTED:
                await dispatcher.dispatch_alert(parse_overheat_event(name, data))
            elif name == EVENT_OVERHEAT_CLEARED:
                await dispatcher.dispatch_clear(parse_overheat_event(name, data))
            elif name in (EVENT_SMART_DEGRADED, EVENT_SMART_RECOVERED):
                await dispatcher.dispatch_smart(parse_smart_change(name, data))
            elif name == EVENT_HOST_DEGRADED:
                await dispatcher.dispatch_host_alert(parse_host_event(name, data))
            elif name == EVENT_HOST_RECOVERED:
                await dispatcher.dispatch_host_clear(parse_host_event(name, data))
            else:
                log.info("Событие монитора без обработчика: %s", name)
        except (KeyError, ValueError, TypeError) as exc:
            log.warning("Невалидное событие монитора %r: %s", name, exc)

    return handle
