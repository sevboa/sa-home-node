from __future__ import annotations

import sys
from collections.abc import Callable
from typing import get_args

from sa_home_node.config import DEFAULT_CONFIG
from sa_home_node.swarm.kind import KIND_WORKSTATION, NodeKind

Reply = Callable[[str], str]

_KNOWN_KINDS = frozenset(get_args(NodeKind))


def prompt(reply: Reply, question: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = reply(f"{question}{suffix}: ").strip()
    except EOFError:
        return default
    return answer or default


def prompt_yes_no(reply: Reply, question: str, default: bool) -> bool:
    hint = "Y/n" if default else "y/N"
    try:
        answer = reply(f"{question} [{hint}]: ").strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer in ("y", "yes", "д", "да")


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def resolve_kind(value: str | None, interactive: bool, reply: Reply) -> str:
    if value is not None:
        return value
    if not interactive:
        return KIND_WORKSTATION
    kind = prompt(reply, "Тип машины (server/workstation/vps)", KIND_WORKSTATION)
    if kind not in _KNOWN_KINDS:
        print(f"Неизвестный тип {kind!r}, беру workstation", file=sys.stderr)
        return KIND_WORKSTATION
    return kind


def resolve_assignments(value: str | None, interactive: bool, reply: Reply) -> list[str]:
    if value is not None:
        return split_csv(value)
    if not interactive:
        return []
    return split_csv(prompt(reply, "Что нода поднимает (через запятую, пусто — просто вступить в рой)", ""))


def resolve_listen(value: str | None, interactive: bool, reply: Reply, kind: str) -> list[str]:
    if value is not None:
        return split_csv(value)
    if kind != "vps":
        return ["tcp://0.0.0.0:8710"]
    if not interactive:
        print(
            "listen пуст (kind=vps) — нода не сможет вступить в рой, пока не задашь --listen явно",
            file=sys.stderr,
        )
        return []
    listen = split_csv(
        prompt(
            reply,
            "Публичная машина (vps) — адрес(а) для входящих соединений от "
            "пиров, НЕ 0.0.0.0: явный приватный адрес, иначе рой "
            "торчит в интернет",
            "",
        )
    )
    while not listen and not prompt_yes_no(
        reply,
        "Пусто — нода не сможет вступить в рой вовсе (swarm_join требует наш адрес). Точно оставить пустым?",
        False,
    ):
        listen = split_csv(prompt(reply, "Адрес(а) для входящих соединений от пиров", ""))
    return listen


def resolve_join(value: str | None, interactive: bool, reply: Reply) -> tuple[str, bool]:
    if value is not None:
        return value, False
    if not interactive:
        return "", False
    founding_new_swarm = not prompt_yes_no(
        reply, "В этой локальной сети уже есть рой (другие ноды sa-home)?", True
    )
    if founding_new_swarm:
        return "", True
    join = prompt(
        reply,
        "IP:порт соседа, если знаешь (пусто — понадеемся на LAN-маячок: "
        "он сам найдёт и присоединится по общему токену)",
        "",
    )
    return join, False


def resolve_token(
    value: str | None,
    interactive: bool,
    reply: Reply,
    join: str,
    founding_new_swarm: bool,
    generate: Callable[[], str],
) -> str | None:
    if value is not None:
        return value
    must_paste_token = bool(join) or (interactive and not founding_new_swarm)
    if not must_paste_token:
        if not interactive:
            return generate()
        generated = generate()
        if prompt_yes_no(
            reply,
            f"Это первая нода — сгенерировать новый токен роя ({generated[:8]}…)?",
            True,
        ):
            return generated
        return prompt(reply, "Вставь токен")
    if not interactive:
        return None
    token = prompt(
        reply,
        "Токен роя (тот же, что в config.toml существующей ноды — "
        "см. `grep -A3 '^\\[swarm\\]' " + str(DEFAULT_CONFIG) + "` на ней)",
    )
    while not token:
        token = prompt(reply, "Токен обязателен, повтори")
    return token
