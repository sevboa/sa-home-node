from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sa_home_node.proto.wire import ERR_UNKNOWN_ACTION, NODE_SERVICE, Address, ProtoError
from sa_home_node.services.periodic import PEER_ERRORS
from sa_home_node.wake import peers
from sa_home_node.wake.peers import NodeLink, WakeCache

log = logging.getLogger(__name__)

PEER_TIMEOUT_S = 3.0
PRESENCE_TIMEOUT_S = 6.0
WAKE_POLL_TIMEOUT_S = 180.0
WAKE_POLL_INTERVAL_S = 3.0
WARMUP_TIMEOUT_S = 360.0

READY = "ready"
UNREACHABLE = "unreachable"
WARMUP_FAILED = "warmup_failed"
CRASHED = "crashed"

PHASE_WAKING = "waking"
PHASE_WOKEN = "woken"
PHASE_WARMING = "warming"

WakeGuard = Callable[[str], Awaitable[str | None]]
PhaseCallback = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class WakeOutcome:
    ok: bool
    detail: str


async def wait_for_service(
    link: NodeLink, node_id: str, service: str, timeout_s: float, interval_s: float = 3.0
) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    dst = Address(node=node_id, service=service)
    while True:
        if await peers.fetch_state(link, dst) is not None:
            return True
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(interval_s)


async def try_warmup(link: NodeLink, dst: Address, timeout_s: float = WARMUP_TIMEOUT_S) -> bool:
    try:
        await link.command("warmup", {}, dst=dst, timeout=timeout_s)
        return True
    except ProtoError as exc:
        if exc.code == ERR_UNKNOWN_ACTION:
            return True
        log.warning(
            "wake: %s/%s отказал в прогреве: %s — %s", dst.node, dst.service, exc.code, exc.message
        )
        return False
    except PEER_ERRORS as exc:
        log.warning(
            "wake: %s/%s не ответил на прогрев за %.0f с (%s)",
            dst.node, dst.service, timeout_s, type(exc).__name__,
        )
        return False


async def ensure_service_ready(
    link: NodeLink,
    cache: WakeCache,
    node_id: str,
    service: str,
    *,
    wake_timeout_s: float | None = None,
    wake_poll_interval_s: float = WAKE_POLL_INTERVAL_S,
    warmup_timeout_s: float = WARMUP_TIMEOUT_S,
    on_phase_change: PhaseCallback | None = None,
    guard: WakeGuard | None = None,
) -> str:
    if wake_timeout_s is None:
        wake_timeout_s = WAKE_POLL_TIMEOUT_S
    dst = Address(node=node_id, service=service)

    async def finish_warmup() -> str:
        if await try_warmup(link, dst, warmup_timeout_s):
            return READY
        log.warning("wake: прогрев %s/%s не удался", node_id, service)
        return WARMUP_FAILED

    state = await peers.fetch_state(link, dst, timeout_s=PRESENCE_TIMEOUT_S)

    if state is None:
        node_alive = service != NODE_SERVICE and (
            await peers.fetch_state(
                link, Address(node=node_id, service=NODE_SERVICE), timeout_s=PRESENCE_TIMEOUT_S
            )
            is not None
        )
        if node_alive:
            log.warning(
                "wake: нода «%s» жива, но служба %s не отвечает — WoL тут бесполезен",
                node_id, service,
            )
            return CRASHED
        if on_phase_change is not None:
            await on_phase_change(PHASE_WAKING)
        outcome = await wake_swarm_node_core(link, cache, node_id, guard=guard)
        if not outcome.ok:
            log.warning("wake: не удалось разбудить «%s»: %s", node_id, outcome.detail)
            return UNREACHABLE
        log.info("wake: «%s» разбужена, ждём %s до %.0f с", node_id, service, wake_timeout_s)
        if not await wait_for_service(link, node_id, service, wake_timeout_s, wake_poll_interval_s):
            log.warning(
                "wake: «%s» не подняла службу %s за %.0f с", node_id, service, wake_timeout_s
            )
            return UNREACHABLE
        if on_phase_change is not None:
            await on_phase_change(PHASE_WOKEN)
            await on_phase_change(PHASE_WARMING)
        return await finish_warmup()

    if state.get("asleep"):
        if on_phase_change is not None:
            await on_phase_change(PHASE_WARMING)
        return await finish_warmup()

    return READY


async def wake_swarm_node_core(
    link: NodeLink,
    cache: WakeCache,
    node_id: str,
    *,
    force: bool = False,
    guard: WakeGuard | None = None,
) -> WakeOutcome:
    if not force and guard is not None:
        denial = await guard(node_id)
        if denial is not None:
            return WakeOutcome(False, denial)
    info = await peers.resolve_wake_info(link, cache, node_id)
    if info is None:
        return WakeOutcome(
            False, f"⚙️ Нет данных о MAC «{node_id}» — нода ещё ни разу не была видна в рое."
        )

    waker = await peers.find_lan_waker(link, cache, node_id, info["broadcast"])
    if waker is None:
        return WakeOutcome(
            False, f"⚠️ Некому отправить сигнал: нет живой ноды в той же сети, что «{node_id}»."
        )

    dst = Address(node=waker, service=NODE_SERVICE)
    try:
        await link.command("send_wol", {"mac": info["mac"]}, dst=dst)
    except ProtoError as exc:
        return WakeOutcome(False, f"❌ {waker}: {exc.message}")
    except PEER_ERRORS:
        return WakeOutcome(False, f"⚠️ Нода «{waker}» перестала отвечать во время отправки.")

    return WakeOutcome(
        True,
        f"🔌 Magic packet для «{node_id}» (<code>{info['mac']}</code>) отправлен через "
        f"ноду «{waker}». Появится в /nodes, как поднимется.",
    )
