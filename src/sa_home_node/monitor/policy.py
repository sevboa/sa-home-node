from __future__ import annotations

from dataclasses import dataclass

from sa_home_node.monitor.models import (
    BAND_MID,
    BAND_OVER,
    BAND_UNDER,
    BaselineStats,
    SensorReading,
)


def _band(temp: float, warn: float, hysteresis: float) -> str:
    if temp >= warn:
        return BAND_OVER
    if temp <= warn - hysteresis:
        return BAND_UNDER
    return BAND_MID


@dataclass(frozen=True)
class FixedThresholdPolicy:
    warn_c: float
    crit_c: float
    hysteresis_delta_c: float

    def band(self, reading: SensorReading) -> str:
        return _band(reading.temperature_c, self.warn_c, self.hysteresis_delta_c)


@dataclass(frozen=True)
class BaselinePolicy:
    warn_c: float
    crit_c: float
    hysteresis_delta_c: float
    stats: BaselineStats
    min_samples: int
    k_sigma: float
    min_std_c: float

    def _effective_warn(self) -> float:
        if self.stats.count < self.min_samples:
            return self.warn_c
        dynamic = self.stats.mean + self.k_sigma * max(self.stats.std, self.min_std_c)
        return min(self.warn_c, dynamic)

    def band(self, reading: SensorReading) -> str:
        return _band(reading.temperature_c, self._effective_warn(), self.hysteresis_delta_c)
