import asyncio
import logging

from pydantic import BaseModel

from sa_home_node.plugins import NodeAction, Plugin, ServiceContext, ServiceSpec
from sa_home_node.proto.describe import (
    ActionParam,
    ActionSpec,
    ServiceDescription,
    ServiceInfo,
)
from sa_home_node.proto.wire import ERR_UNKNOWN_ACTION, ProtoError

log = logging.getLogger(__name__)


class ExampleConfig(BaseModel):
    interval_s: float = 60.0
    socket: str = "./data/example.sock"


async def hello(ctx: ServiceContext) -> None:
    while True:
        text = ctx.content.get("example.greeting")
        log.info(text)
        if ctx.swarm is not None:
            await ctx.swarm.emit("example_greeting", {"text": text})
        await asyncio.sleep(ctx.config.interval_s)


async def echo(ctx: ServiceContext) -> None:
    await asyncio.Event().wait()


async def example_ping(args) -> dict:
    return {"pong": args.get("text", "")}


class EchoHandler:
    def __init__(self, ctx: ServiceContext) -> None:
        self.node_id = ctx.node_id
        self.count = 0

    def describe(self) -> ServiceDescription:
        return ServiceDescription(
            info=ServiceInfo(node=self.node_id, service="echo", version="1"),
            actions=(
                ActionSpec(
                    id="echo",
                    title="Эхо",
                    params=(ActionParam(name="text", required=True),),
                ),
            ),
        )

    async def get_state(self) -> dict:
        return {"count": self.count}

    async def run_command(self, action: str, args: dict) -> dict:
        if action != "echo":
            raise ProtoError(ERR_UNKNOWN_ACTION, f"нет такого действия: {action}")
        self.count += 1
        return {"text": args["text"]}


PLUGIN = Plugin(
    name="example",
    config_model=ExampleConfig,
    services=(
        ServiceSpec("hello", hello),
        ServiceSpec(
            "echo",
            echo,
            handler=EchoHandler,
            process=True,
            endpoint=lambda config: config.socket,
        ),
    ),
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
