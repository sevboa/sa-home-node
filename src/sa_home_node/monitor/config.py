from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sa_home_node.swarm.kind import KIND_VPS


class ScheduleConfig(BaseModel):
    scan_cron: str = "*/1 * * * *"
    smart_cron: str = "0 * * * *"
    housekeeping_cron: str = "0 3 * * *"


class _BaselineParams(BaseModel):
    mode: Literal["fixed", "baseline"] = "fixed"
    baseline_window: int = Field(default=240, ge=1)
    baseline_min_samples: int = Field(default=30, ge=1)
    baseline_k_sigma: float = Field(default=4.0, gt=0)
    baseline_min_std_c: float = Field(default=3.0, ge=0)


class CpuSensorConfig(_BaselineParams):
    enabled: bool = True
    warn_c: float = 80.0
    crit_c: float = 90.0
    hysteresis_delta_c: float = 5.0
    consecutive_to_alert: int = Field(default=3, ge=1)
    consecutive_to_clear: int = Field(default=3, ge=1)


class DiskSensorConfig(_BaselineParams):
    enabled: bool = True
    warn_c: float = 55.0
    crit_c: float = 65.0
    hysteresis_delta_c: float = 5.0
    consecutive_to_alert: int = Field(default=2, ge=1)
    consecutive_to_clear: int = Field(default=2, ge=1)
    devices: list[str] = Field(default_factory=list)


class GpuSensorConfig(_BaselineParams):
    enabled: bool = False
    warn_c: float = 80.0
    crit_c: float = 90.0
    hysteresis_delta_c: float = 5.0
    consecutive_to_alert: int = Field(default=3, ge=1)
    consecutive_to_clear: int = Field(default=3, ge=1)


class HostMetricThreshold(BaseModel):
    warn: float
    crit: float


class HostSensorConfig(BaseModel):
    enabled: bool | None = None
    sample_window_s: float = Field(default=10.0, gt=0)
    consecutive_to_alert: int = Field(default=3, ge=1)
    consecutive_to_clear: int = Field(default=3, ge=1)
    thresholds: dict[str, HostMetricThreshold] = Field(default_factory=dict)


class SensorsConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    cpu: CpuSensorConfig = Field(default_factory=CpuSensorConfig)
    gpu: GpuSensorConfig = Field(default_factory=GpuSensorConfig)
    disks: DiskSensorConfig = Field(default_factory=DiskSensorConfig)
    host: HostSensorConfig = Field(default_factory=HostSensorConfig)


class MonitorConfig(BaseModel):
    socket: str = "./data/monitor.sock"
    db_path: Path = Path("./data/monitor.sqlite")


def apply_node_kind(sensors: SensorsConfig, kind: str) -> SensorsConfig:
    is_vps = kind == KIND_VPS
    result = sensors.model_copy(deep=True)
    if result.host.enabled is None:
        result.host.enabled = is_vps
    if is_vps:
        for sensor in (result.cpu, result.disks):
            if "enabled" not in sensor.model_fields_set:
                sensor.enabled = False
    return result
