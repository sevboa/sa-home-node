from sa_home_node.bot.config import BotServiceConfig
from sa_home_node.bot.run import run_bot
from sa_home_node.plugins import Plugin, ServiceSpec

PLUGIN = Plugin(
    name="bot",
    config_model=BotServiceConfig,
    services=(
        ServiceSpec(
            "telegram-bot",
            run_bot,
            process=True,
            singleton=True,
            instanced=True,
            satellites=(".guests",),
        ),
    ),
)
