from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sa_home_node.metrics.config import MetricsConfig


@dataclass(frozen=True)
class ScrapeJobSpec:
    name: str
    targets_file: str
    port: Callable[[MetricsConfig], int]
    nodes: Callable[[], Collection[str]] = lambda: ()


@dataclass(frozen=True)
class DashboardSpec:
    file: str
    build: Callable[[], dict]
