from __future__ import annotations

import logging
from typing import Any

from sa_home_node import wol
from sa_home_node.proto.describe import ActionParam, ActionSpec
from sa_home_node.proto.wire import ERR_BAD_REQUEST, ERR_INTERNAL, ProtoError
from sa_home_node.swarm.service import NodeService

log = logging.getLogger(__name__)

ACTION_SEND_WOL = "send_wol"
SEND_WOL_TITLE = "📡 Отправить Wake-on-LAN"


def local_wake_payload() -> dict[str, str] | None:
    info = wol.detect_local_wake_info()
    if info is None:
        return None
    return {"mac": info.mac, "ip": info.ip, "broadcast": info.broadcast}


def register_wake(node_service: NodeService) -> None:
    async def send_wol(args: dict[str, Any]) -> dict[str, Any]:
        try:
            mac = wol.normalize_mac(str(args.get("mac", "")))
        except ValueError as exc:
            raise ProtoError(ERR_BAD_REQUEST, str(exc)) from exc
        info = wol.detect_local_wake_info()
        try:
            wol.send_magic_packet(mac, bind_ip=info.ip if info is not None else "")
        except OSError as exc:
            raise ProtoError(ERR_INTERNAL, f"не удалось отправить magic packet: {exc}") from exc
        log.warning("Wake-on-LAN: %s разослала magic packet для %s", node_service.node_id, mac)
        return {"sent": True, "mac": mac}

    spec = ActionSpec(
        id=ACTION_SEND_WOL,
        title=SEND_WOL_TITLE,
        params=(ActionParam(name="mac", type="string", required=True, title="MAC цели"),),
    )
    node_service.actions.add(spec, send_wol)
