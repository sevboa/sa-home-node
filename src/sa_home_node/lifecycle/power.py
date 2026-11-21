from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from sa_home_node import host
from sa_home_node.proto.describe import ActionSpec
from sa_home_node.swarm.kind import traits_for
from sa_home_node.swarm.router import NodeRouter
from sa_home_node.swarm.service import NodeService

log = logging.getLogger(__name__)

ACTION_POWEROFF = "poweroff"
ACTION_REBOOT = "reboot"
ACTION_SUSPEND = "suspend"
ACTION_RESTART_NODE = "restart_node"

POWER_TITLES = {
    ACTION_POWEROFF: "⏻ Выключить машину", ACTION_REBOOT: "🔃 Перезагрузить машину",
    ACTION_SUSPEND: "🌙 Усыпить машину",
}
RESTART_NODE_TITLE = "🔄 Перезапустить ноду"

POWER_DELAY_S = 1.0
RESTART_DEFER_POLL_S = 30.0
RESTART_DEFER_MAX_S = 1800.0
ARG_FORCE = "force"
ARG_IF_IDLE = "if_idle"

LOGIND_SESSION_PROPS = ("Remote", "Name", "TTY", "Timestamp")

CommandOutput = tuple[int, str, str]
CommandRunner = Callable[[list[str]], Awaitable[CommandOutput]]
Sleep = Callable[[float], Awaitable[Any]]
BlockerProvider = Callable[[], Awaitable[list[str]]]


async def run_command(
    argv: list[str], *, timeout: float | None = None, env: dict[str, str] | None = None
) -> CommandOutput:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except BaseException:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise
    code = proc.returncode if proc.returncode is not None else -1
    return code, stdout.decode(errors="replace"), stderr.decode(errors="replace")


def parse_properties(raw: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)


async def ssh_sessions(run: CommandRunner) -> list[str]:
    try:
        _, listing, _ = await run(["loginctl", "list-sessions", "--no-legend"])
    except OSError:
        return []
    described = []
    for line in listing.splitlines():
        if not line.split():
            continue
        argv = ["loginctl", "show-session", line.split()[0]]
        for prop in LOGIND_SESSION_PROPS:
            argv += ["-p", prop]
        props = parse_properties((await run(argv))[1])
        if props.get("Remote") == "yes":
            described.append(
                f"{props.get('Name', '?')}, {props.get('TTY') or '?'}, с {props.get('Timestamp') or '?'}"
            )
    return described


async def tmux_sessions(run: CommandRunner) -> list[str]:
    try:
        _, raw, _ = await run(["tmux", "list-sessions", "-F", "#{session_name}"])
    except OSError:
        return []
    return [f"tmux: {line}" for line in raw.splitlines() if line.strip()]


class PowerActions:
    def __init__(
        self,
        *,
        router: NodeRouter,
        request_restart: Callable[[], None],
        blockers: Sequence[BlockerProvider] = (),
        run: CommandRunner = run_command,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._router = router
        self._request_restart = request_restart
        self._blockers = tuple(blockers)
        self._run = run
        self._sleep = sleep
        self._tasks: set[asyncio.Task] = set()
        self._restart_wait: asyncio.Task | None = None

    def register(self, node_service: NodeService, *, power: bool) -> None:
        actions = node_service.actions
        actions.add(ActionSpec(id=ACTION_RESTART_NODE, title=RESTART_NODE_TITLE), self._restart_node)
        if power:
            for action, title in POWER_TITLES.items():
                actions.add(ActionSpec(id=action, title=title), self._power_handler(action))

    def _keep(self, name: str, work: Awaitable[None]) -> None:
        task = asyncio.ensure_future(work)
        task.set_name(name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _later(self, name: str, work: Callable[[], Awaitable[None]]) -> None:
        async def run() -> None:
            await self._sleep(POWER_DELAY_S)
            await work()

        self._keep(name, run())

    def _power_handler(self, action: str):
        async def handler(args: dict[str, Any]) -> dict[str, Any]:
            if action == ACTION_POWEROFF and args.get(ARG_IF_IDLE) is True:
                return await self._poweroff_if_idle()
            return self._schedule_power(action)

        return handler

    async def _poweroff_if_idle(self) -> dict[str, Any]:
        blockers = await self._collect_blockers()
        if blockers:
            log.info("выключение по просьбе отложено — %s", ", ".join(blockers))
            return {"done": False, "blockers": blockers}
        self._schedule_power(ACTION_POWEROFF)
        return {"done": True, "blockers": []}

    async def _collect_blockers(self) -> list[str]:
        busy = self._router.busy_services()
        blockers = ["службы: " + ", ".join(busy)] if busy else []
        blockers += await ssh_sessions(self._run)
        blockers += await tmux_sessions(self._run)
        for provider in self._blockers:
            blockers += await provider()
        return blockers

    def _schedule_power(self, action: str) -> dict[str, Any]:
        argv = host.current().power_argv(action)
        log.warning("Power-действие %s: выполняю %s через %.0f с", action, argv, POWER_DELAY_S)

        async def work() -> None:
            code, _, stderr = await self._run(argv)
            if code != 0:
                log.error("Power-команда %s завершилась с кодом %s: %s", argv, code, stderr.strip())

        self._later(f"power-{action}", work)
        return {"scheduled": action, "delay_s": POWER_DELAY_S}

    async def _restart_node(self, args: dict[str, Any]) -> dict[str, Any]:
        force = bool(args.get(ARG_FORCE))
        waiting = self._restart_wait is not None and not self._restart_wait.done()
        if force and waiting:
            self._restart_wait.cancel()
            self._restart_wait = None
            waiting = False
        busy = [] if force else self._working_or_idle()
        if not (busy or waiting):
            return self._restart_now()
        if not waiting:
            log.warning(
                "Запрошен само-рестарт ноды, службы заняты (%s) — жду до %.0f с",
                ", ".join(busy),
                RESTART_DEFER_MAX_S,
            )
            self._restart_wait = asyncio.ensure_future(self._restart_when_idle())
            self._restart_wait.set_name("restart-node-wait")
        busy = busy or self._working_or_idle()
        message = (
            f"рестарт отложен до освобождения: {', '.join(busy)}" if busy else "ожидание рестарта уже идёт"
        )
        return {"deferred": busy, "max_wait_s": RESTART_DEFER_MAX_S, "message": message}

    def _working_or_idle(self) -> list[str]:
        try:
            return self._router.working_services()
        except Exception:
            log.exception("Не удалось узнать занятость служб")
            return []

    def _restart_now(self) -> dict[str, Any]:
        log.warning("Запрошен само-рестарт ноды через %.0f с", POWER_DELAY_S)

        async def work() -> None:
            self._request_restart()

        self._later("restart-node", work)
        return {"scheduled": ACTION_RESTART_NODE, "delay_s": POWER_DELAY_S}

    async def _restart_when_idle(self) -> None:
        try:
            waited = 0.0
            while waited < RESTART_DEFER_MAX_S:
                await self._sleep(RESTART_DEFER_POLL_S)
                waited += RESTART_DEFER_POLL_S
                if not self._router.working_services():
                    log.warning("Службы освободились — перезапускаю ноду")
                    break
            else:
                log.warning(
                    "Службы заняты дольше %.0f с (%s) — перезапускаю ноду принудительно",
                    RESTART_DEFER_MAX_S,
                    ", ".join(self._working_or_idle()),
                )
        except Exception:
            log.exception("Ожидание рестарта сорвалось — перезапускаю ноду принудительно")
        self._restart_now()


def register_power(
    node_service: NodeService, *, kind: str, power_controllable: bool | None, **kwargs: Any
) -> None:
    if power_controllable is None:
        power_controllable = traits_for(kind).power_controllable
    PowerActions(**kwargs).register(node_service, power=power_controllable)
