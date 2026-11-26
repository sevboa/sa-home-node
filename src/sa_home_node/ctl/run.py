from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from sa_home_node.ctl.calls import parse_call_args, render_describe
from sa_home_node.ctl.cli import resolve_target
from sa_home_node.ctl.render import (
    render_check_update,
    render_event,
    render_services,
    render_status,
    render_update_result,
)
from sa_home_node.proto.client import ProtoClient
from sa_home_node.proto.wire import Address, Envelope, ProtoError

NO_ARG_ACTIONS = ("restart_node", "poweroff", "reboot", "suspend")


async def run(args: argparse.Namespace) -> int:
    endpoint, token = resolve_target(args)
    dst = Address(node=args.node, service="node") if args.node else None

    events_mode = args.command == "events"

    async def on_event(env: Envelope) -> None:
        print(render_event(env, datetime.now()), flush=True)

    client = ProtoClient(endpoint, token=token, on_event=on_event if events_mode else None)
    try:
        await client.connect()
    except (ConnectionError, OSError, ProtoError) as exc:
        print(f"Нода недоступна ({endpoint}): {exc}", file=sys.stderr)
        return 1

    try:
        if args.command == "status":
            print(render_status(await client.get_state(dst=dst)))
        elif args.command in ("start", "stop", "restart", "assign"):
            result = await client.command(args.command, {"name": args.name}, dst=dst)
            print(render_services([result["service"]]))
        elif args.command == "unassign":
            result = await client.command(args.command, {"name": args.name}, dst=dst)
            print(f"Снято: {result.get('unassigned', args.name)}")
        elif args.command == "join":
            result = await client.command("join", {"endpoint": args.endpoint}, dst=dst)
            added = result.get("peers_added") or []
            print(
                f"Присоединились через {args.endpoint}. Новых пиров: {', '.join(added) if added else 'нет'}."
            )
        elif args.command == "call":
            try:
                call_args = parse_call_args(args.args)
            except ValueError as exc:
                print(f"Ошибка: {exc}", file=sys.stderr)
                return 2
            extra = {"timeout": args.timeout} if args.timeout else {}
            result = await client.command(
                args.action, call_args, dst=Address(node=args.node, service=args.service), **extra
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "describe":
            desc = await client.describe(dst=Address(node=args.node, service=args.service))
            print(render_describe(desc))
        elif args.command == "check_update":
            print(render_check_update(await client.command("check_update", dst=dst)))
        elif args.command == "update":
            print(render_update_result(await client.command("update", dst=dst)))
        elif args.command in NO_ARG_ACTIONS:
            force = {"force": True} if getattr(args, "force", False) else None
            result = await client.command(args.command, force, dst=dst)
            where = f"нода {args.node}" if args.node else "нода"
            if result.get("deferred") is not None:
                print(f"{where}: {result.get('message', 'рестарт отложен')}.")
                return 0
            print(
                f"Принято: {where} выполнит {result.get('scheduled', args.command)} "
                f"через {result.get('delay_s', '?')} с."
            )
        elif events_mode:
            info = await client.hello()
            print(f"События ноды {info.node} (Ctrl+C — выход):", flush=True)
            await client.join()
            print("Соединение закрыто нодой.", file=sys.stderr)
    except ProtoError as exc:
        print(f"Ошибка: {exc.message}", file=sys.stderr)
        return 1
    except TimeoutError:
        print("Нода не ответила вовремя — попробуйте ещё раз.", file=sys.stderr)
        return 1
    finally:
        await client.close()
    return 0
