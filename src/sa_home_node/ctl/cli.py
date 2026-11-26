from __future__ import annotations

import argparse
from pathlib import Path

from sa_home_node.config import load_config
from sa_home_node.proto.endpoints import Endpoint, parse_endpoint, resolve_endpoint
from sa_home_node.swarm.config import swarm_section


def build_parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description="Консоль управления нодой sa-home.")
    parser.add_argument("--config", "-c", type=Path, default=None, help="путь к config.toml")
    parser.add_argument("--socket", "-s", default=None, help="endpoint ноды: путь сокета или tcp://host:port")
    parser.add_argument(
        "--node",
        "-n",
        default=None,
        help="id удалённой ноды роя: запрос пойдёт через свою ноду",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="статус ноды и её служб")

    for action in ("start", "stop", "restart"):
        p = sub.add_parser(action, help=f"{action} службу")
        p.add_argument("name", help="имя службы (см. nodectl status)")

    p = sub.add_parser("assign", help="назначить службу ноде (без правки конфига)")
    p.add_argument("name", help="имя службы")

    p = sub.add_parser("unassign", help="снять назначение службы")
    p.add_argument("name", help="имя службы (см. nodectl status)")

    p = sub.add_parser("join", help="присоединиться к рою через уже существующую ноду")
    p.add_argument("endpoint", help="endpoint соседа: tcp://host:port")

    p = sub.add_parser("restart_node", help="перезапустить саму ноду (супервизор)")
    p.add_argument(
        "--force",
        action="store_true",
        help="не ждать занятых служб (по умолчанию рестарт откладывается до 30 мин)",
    )

    sub.add_parser("poweroff", help="выключить машину ноды")
    sub.add_parser("reboot", help="перезагрузить машину ноды")
    sub.add_parser("suspend", help="усыпить машину ноды")
    sub.add_parser("events", help="живой хвост событий ноды (Ctrl+C — выход)")

    p = sub.add_parser("call", help="вызвать любое умение любой службы роя")
    p.add_argument("service", help="имя службы")
    p.add_argument("action", help="id действия (см. nodectl describe <служба>)")
    p.add_argument(
        "args",
        nargs="*",
        metavar="ключ=значение",
        help="аргументы действия; true/false/числа распознаются по виду",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="СЕК",
        help="сколько ждать ответа (по умолчанию — обычный таймаут протокола)",
    )

    p = sub.add_parser("describe", help="какие умения объявляет служба и с какими параметрами")
    p.add_argument("service", help="имя службы")

    sub.add_parser("check_update", help="сверить работающую/установленную/доступную версии")
    sub.add_parser("update", help="pipx install --force до последнего тега (без рестарта процесса)")

    return parser


def resolve_target(args: argparse.Namespace) -> tuple[Endpoint, str]:
    cfg = load_config(args.config)
    token = swarm_section(cfg).token
    if args.socket is not None:
        endpoint = parse_endpoint(args.socket)
    else:
        endpoint = resolve_endpoint(cfg.node.socket, cfg.path.parent)
    return endpoint, token
