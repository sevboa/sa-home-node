import asyncio
import logging

from pydantic import BaseModel

from sa_home_node.plugins import NodeAction, Plugin, ServiceContext, ServiceSpec
from sa_home_node.proto.describe import ActionParam, ActionSpec

log = logging.getLogger(__name__)


class ExampleConfig(BaseModel):
    interval_s: float = 60.0


async def hello(ctx: ServiceContext) -> None:
    while True:
        text = ctx.content.get("example.greeting")
        log.info(text)
        if ctx.swarm is not None:
            await ctx.swarm.emit("example_greeting", {"text": text})
        await asyncio.sleep(ctx.config.interval_s)


async def example_ping(args) -> dict:
    return {"pong": args.get("text", "")}


PLUGIN = Plugin(
    name="example",
    config_model=ExampleConfig,
    services=(ServiceSpec("hello", hello),),
    content_keys=("example.greeting",),
    node_actions=(
        NodeAction(
            ActionSpec(
                id="example_ping",
                title="Пинг примера",
                params=(ActionParam(name="text", required=False),),
            ),
            example_ping,
        ),
    ),
)
