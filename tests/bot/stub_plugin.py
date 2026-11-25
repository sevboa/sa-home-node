from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command as CommandFilter
from aiogram.types import Message

from sa_home_node.bot.broadcast import broadcast
from sa_home_node.bot.commands import Command
from sa_home_node.bot.context import BotContext
from sa_home_node.bot.gate import Admission
from sa_home_node.bot.rights import GuestRight, RightGroup
from sa_home_node.bot.spec import BotSpec, EventRoute, RouterSpec
from sa_home_node.plugins import Plugin
from sa_home_node.proto.wire import Envelope

HELLO_RIGHT = "hello@stub"
HELLO_TEXT = "Привет из заглушки"
WELCOME_TEXT = "Добро пожаловать в заглушку"
PING_EVENT = "stub_ping"


def _build_router(ctx: BotContext) -> Router:
    router = Router(name="stub")

    @router.message(CommandFilter("hello"))
    async def hello(message: Message) -> None:
        await message.answer(HELLO_TEXT)

    return router


async def _relay_ping(ctx: BotContext, env: Envelope) -> None:
    text = f"stub: {env.payload['data']['text']}"
    await broadcast(ctx.book.accepting(PING_EVENT), ctx.notifier, text)


def stub_plugin(admitted: list[tuple[int, int]]) -> Plugin:
    async def on_admitted(message: Message, admission: Admission) -> None:
        admitted.append((message.chat.id, admission.invited_by_chat_id))
        await message.answer(WELCOME_TEXT)

    return Plugin(
        name="stub",
        bot=BotSpec(
            commands=(Command(name="hello", description="приветствие", universal=False, right=HELLO_RIGHT),),
            routers=(RouterSpec(order=500, build=_build_router),),
            guest_rights=(RightGroup("stub", "Заглушка", (GuestRight(HELLO_RIGHT, "Приветствие"),)),),
            event_routes=(EventRoute(frozenset({PING_EVENT}), _relay_ping),),
            on_admitted=on_admitted,
        ),
    )
