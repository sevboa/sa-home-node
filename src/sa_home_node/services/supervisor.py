from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Callable, Iterable

from sa_home_node.plugins import ServiceSpec
from sa_home_node.services.assignments import Assignment, AssignmentError, parse
from sa_home_node.services.registry import ServiceTable
from sa_home_node.services.slot import SlotBase

log = logging.getLogger(__name__)


SlotFactory = Callable[[ServiceSpec, Assignment], SlotBase]


def process_argv(service: str, instance: str, config_path: str | None) -> list[str]:
    argv = [sys.executable, "-m", "sa_home_node", "service", service]
    if instance:
        argv.append(f"--instance={instance}")
    if config_path is not None:
        argv += ["--config", config_path]
    return argv


class Supervisor:
    def __init__(
        self, assignments: Iterable[str], table: ServiceTable, factory: SlotFactory, auto: Iterable[str] = ()
    ) -> None:
        self.services: dict[str, SlotBase] = {}
        self._table = table
        self._factory = factory
        for item in assignments:
            try:
                assignment = parse(item)
            except AssignmentError as exc:
                log.error("Назначение %r не разбирается (%s) — пропускаю", item, exc)
                continue
            spec = table.spec(assignment.service)
            if spec is not None and spec.external:
                log.info("Назначение %r — внешняя служба, супервизор её не держит", item)
                continue
            try:
                self.services[assignment.key] = self.make(assignment)
            except ValueError:
                log.error(
                    "Неизвестное назначение %r — пропускаю (знаю: %s)",
                    item,
                    ", ".join(table.supervised_names()),
                )
        for name in auto:
            self._add_auto(name)

    def make(self, assignment: Assignment) -> SlotBase:
        spec = self._table.spec(assignment.service)
        if spec is None or spec.external:
            raise ValueError(f"неизвестное назначение: {assignment.service!r}")
        return self._factory(spec, assignment)

    def _add_auto(self, name: str) -> None:
        if name in self.services:
            return
        try:
            self.services[name] = self.make(Assignment(service=name))
        except ValueError:
            log.warning("Автослужба %r неизвестна — пропускаю", name)

    async def start_all(self) -> None:
        for svc in self.services.values():
            spec = self._table.spec(svc.assignment.service)
            if spec is not None and spec.singleton:
                log.info("Служба %s — синглтон: запуск решает аренда лидерства", svc.name)
                continue
            await svc.start()

    async def stop_all(self) -> None:
        results = await asyncio.gather(
            *(svc.stop() for svc in reversed(list(self.services.values()))), return_exceptions=True
        )
        for result in results:
            if isinstance(result, BaseException):
                log.warning("Останов службы не удался: %r", result)

    def get(self, name: str) -> SlotBase | None:
        return self.services.get(name)

    def assign(self, text: str) -> SlotBase:
        assignment = parse(text)
        existing = self.services.get(assignment.key)
        if existing is not None:
            return existing
        svc = self.make(assignment)
        self.services[assignment.key] = svc
        return svc

    async def unassign(self, name: str) -> None:
        svc = self.services.pop(name, None)
        if svc is not None:
            await svc.stop()
