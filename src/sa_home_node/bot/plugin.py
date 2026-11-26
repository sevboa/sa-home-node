from sa_home_node.bot.config import BotServiceConfig
from sa_home_node.plugins import Plugin, ServiceContext, ServiceSpec


async def run_telegram_bot(ctx: ServiceContext) -> bool:
    from sa_home_node.bot.run import run_bot

    return await run_bot(ctx)


PLUGIN = Plugin(
    name="bot",
    config_model=BotServiceConfig,
    services=(
        ServiceSpec(
            "telegram-bot",
            run_telegram_bot,
            process=True,
            singleton=True,
            instanced=True,
            satellites=(".guests",),
        ),
    ),
)
