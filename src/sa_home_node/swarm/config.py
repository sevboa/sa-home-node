from __future__ import annotations

from pydantic import BaseModel, Field

from sa_home_node.config import NodeConfig


class SwarmPeer(BaseModel):
    id: str
    endpoint: str
    endpoints: list[str] = []
    kind: str = ""


class DiscoverySection(BaseModel):
    enabled: bool = True
    port: int = Field(default=32167, ge=1, le=65535)
    idle_interval_s: float = Field(default=300.0, gt=0)
    active_interval_s: float = Field(default=15.0, gt=0)


class SwarmSection(BaseModel):
    token: str = ""
    nodes: list[SwarmPeer] = []
    join: str = ""
    node_down_alert_after_s: float = Field(default=300.0, gt=0)
    failover_grace_s: float = Field(default=120.0, gt=0)
    superior_ready_timeout_s: float = Field(default=45.0, gt=0)
    discovery: DiscoverySection = Field(default_factory=DiscoverySection)


def swarm_section(cfg: NodeConfig) -> SwarmSection:
    return cfg.section("swarm", SwarmSection)
