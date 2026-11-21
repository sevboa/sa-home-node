from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import json
import logging
import os
import re
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from sa_home_node import host
from sa_home_node.lifecycle.power import CommandOutput, run_command
from sa_home_node.proto.client import DEFAULT_TIMEOUT
from sa_home_node.proto.describe import ActionSpec
from sa_home_node.proto.wire import ERR_BAD_REQUEST, ERR_INTERNAL, ProtoError
from sa_home_node.services.events import EVENT_UPDATE_FINISHED, EventEmitter
from sa_home_node.swarm.service import NodeService

log = logging.getLogger(__name__)

PACKAGE_NAME = "sa-home-node"

ACTION_CHECK_UPDATE = "check_update"
ACTION_UPDATE = "update"
CHECK_UPDATE_TITLE = "🔍 Проверить обновления"
UPDATE_TITLE = "⬆️ Обновить"

LS_REMOTE_TIMEOUT_S = DEFAULT_TIMEOUT - 3
PIPX_INSTALL_TIMEOUT_S = 300
OUTPUT_TAIL = 2000

TAG_RE = re.compile(r"v\d+(?:\.\d+)*")
EXTRAS_RE = re.compile(r"\s*" + re.escape(PACKAGE_NAME) + r"\s*\[([^\]]*)\]")

UpdateRunner = Callable[..., Awaitable[CommandOutput]]


def version_key(version: str) -> tuple[int, ...]:
    parts = []
    for chunk in version.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def installed_version() -> str | None:
    importlib.invalidate_caches()
    try:
        return importlib.metadata.version(PACKAGE_NAME)
    except importlib.metadata.PackageNotFoundError:
        return None


def origin_repo_url() -> str | None:
    try:
        raw = importlib.metadata.distribution(PACKAGE_NAME).read_text("direct_url.json")
    except importlib.metadata.PackageNotFoundError:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if data.get("dir_info", {}).get("editable"):
        return None
    if (data.get("vcs_info") or {}).get("vcs") != "git":
        return None
    return data.get("url")


def parse_tags(ls_remote_output: str) -> list[str]:
    tags = []
    for line in ls_remote_output.splitlines():
        _, _, ref = line.partition("refs/tags/")
        if ref and TAG_RE.fullmatch(ref):
            tags.append(ref)
    return tags


async def latest_tag(repo_url: str, run: UpdateRunner = run_command) -> str | None:
    try:
        code, stdout, stderr = await run(
            ["git", "ls-remote", "--tags", "--refs", repo_url], timeout=LS_REMOTE_TIMEOUT_S
        )
    except (OSError, TimeoutError) as exc:
        log.warning("git ls-remote %s не сработал: %s", repo_url, exc)
        return None
    if code != 0:
        log.warning("git ls-remote %s вернул код %s: %s", repo_url, code, stderr)
        return None
    tags = parse_tags(stdout)
    if not tags:
        return None
    return max(tags, key=lambda tag: version_key(tag.lstrip("v")))


def pipx_home() -> str:
    return str(Path(sys.executable).parents[3])


def installed_extras() -> str | None:
    meta = Path(sys.executable).parents[1] / "pipx_metadata.json"
    try:
        spec = json.loads(meta.read_text(encoding="utf-8"))["main_package"]["package_or_url"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    match = EXTRAS_RE.match(spec) if isinstance(spec, str) else None
    if not match:
        return None
    extras = ",".join(e.strip() for e in match.group(1).split(",") if e.strip())
    return extras or None


async def pipx_reinstall(repo_url: str, ref: str, run: UpdateRunner = run_command) -> tuple[bool, str]:
    spec = f"git+{repo_url}@{ref}"
    extras = installed_extras()
    if extras:
        spec = f"{PACKAGE_NAME}[{extras}] @ {spec}"
    try:
        code, stdout, stderr = await run(
            ["pipx", "install", "--force", spec],
            timeout=PIPX_INSTALL_TIMEOUT_S,
            env={**os.environ, "PIPX_HOME": pipx_home()},
        )
    except (OSError, TimeoutError) as exc:
        return False, str(exc)
    output = "\n".join(part for part in (stdout.strip(), stderr.strip()) if part)[-OUTPUT_TAIL:]
    return code == 0, output


class UpdateActions:
    def __init__(self, *, repo: str, version: str, emit: EventEmitter, run: UpdateRunner) -> None:
        self._repo = repo
        self._version = version
        self._emit = emit
        self._run = run
        self._updating = False
        self._last: dict[str, Any] | None = None
        self._task: asyncio.Task | None = None

    def register(self, node_service: NodeService) -> None:
        actions = node_service.actions
        actions.add(ActionSpec(id=ACTION_CHECK_UPDATE, title=CHECK_UPDATE_TITLE), self._check)
        actions.add(ActionSpec(id=ACTION_UPDATE, title=UPDATE_TITLE), self._update)
        node_service.state_parts["update"] = self._state

    def _state(self) -> dict[str, Any]:
        installed = installed_version()
        return {
            "running": self._version,
            "installed": installed,
            "restart_required": installed is not None and installed != self._version,
            "last": self._last,
        }

    async def _latest(self) -> str:
        latest = await latest_tag(self._repo, self._run)
        if latest is None:
            raise ProtoError(ERR_INTERNAL, "не удалось проверить обновления (сеть?)")
        return latest

    async def _check(self, args: dict[str, Any]) -> dict[str, Any]:
        latest = await self._latest()
        return {
            "repo": self._repo,
            "running": self._version,
            "installed": installed_version(),
            "latest": latest.lstrip("v"),
        }

    async def _update(self, args: dict[str, Any]) -> dict[str, Any]:
        latest = await self._latest()
        target = latest.lstrip("v")
        if installed_version() == target:
            return {"up_to_date": True, "version": target, "restart_required": self._version != target}
        if self._updating:
            raise ProtoError(ERR_BAD_REQUEST, "обновление уже выполняется")
        self._updating = True
        if host.current().update_via_task:
            self._task = asyncio.create_task(self._run_task(target), name="node-update-task")
            return {"scheduled": True, "target_version": target, "via": "scheduled_task"}
        self._task = asyncio.create_task(self._run_install(latest, target), name="node-update")
        return {"scheduled": True, "target_version": target}

    async def _run_task(self, target: str) -> None:
        try:
            ok, output = await host.current().trigger_update_task()
        except Exception as exc:
            log.exception("Запуск задачи автообновления сорвался")
            ok, output = False, str(exc)
        finally:
            self._updating = False
        if ok:
            log.warning("Задача автообновления запущена — служба скоро перезапустится сама")
            return
        log.error("Не удалось запустить задачу автообновления: %s", output)
        await self._finish(False, target, output)

    async def _run_install(self, ref: str, target: str) -> None:
        try:
            ok, output = await pipx_reinstall(self._repo, ref, self._run)
        except Exception as exc:
            log.exception("Установка обновления сорвалась")
            ok, output = False, str(exc)
        finally:
            self._updating = False
        if ok:
            log.warning("Обновление до %s установлено — нужен restart_node", target)
        else:
            log.error("Обновление до %s не удалось: %s", target, output)
        await self._finish(ok, target, None if ok else output)

    async def _finish(self, ok: bool, target: str, error: str | None) -> None:
        self._last = {"ok": ok, "version": target, "error": error}
        await self._emit(EVENT_UPDATE_FINISHED, self._last)


def register_update(
    node_service: NodeService,
    *,
    repo: str | None,
    emit: EventEmitter,
    run: UpdateRunner = run_command,
) -> None:
    if repo is None:
        return
    UpdateActions(repo=repo, version=node_service.version, emit=emit, run=run).register(node_service)
