import asyncio
import logging

from pydantic import BaseModel

from sa_home_node.plugins import Plugin, ServiceContext, ServiceSpec

log = logging.getLogger(__name__)


class ExampleConfig(BaseModel):
    interval_s: float = 60.0


async def hello(ctx: ServiceContext) -> None:
    while True:
        log.info(ctx.content.get("example.greeting"))
        await asyncio.sleep(ctx.config.interval_s)


PLUGIN = Plugin(
    name="example",
    config_model=ExampleConfig,
    services=(ServiceSpec("hello", hello),),
    content_keys=("example.greeting",),
)
