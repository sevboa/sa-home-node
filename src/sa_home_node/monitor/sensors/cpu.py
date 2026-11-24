from __future__ import annotations

import json
import logging
import shutil
import subprocess
from datetime import datetime

from sa_home_node.monitor.models import KIND_CPU, SensorReading

log = logging.getLogger(__name__)

SENSORS_TIMEOUT_S = 10


def parse_psutil_temps(data: dict, now: datetime) -> list[SensorReading]:
    readings: list[SensorReading] = []
    for chip, entries in data.items():
        for idx, entry in enumerate(entries):
            current = getattr(entry, "current", None)
            if current is None:
                continue
            label = (getattr(entry, "label", "") or "").strip()
            readings.append(
                SensorReading(
                    component_id=f"cpu:{chip}:{label or f'{chip}#{idx}'}",
                    kind=KIND_CPU,
                    label=label or chip,
                    temperature_c=float(current),
                    taken_at=now,
                )
            )
    return readings


def parse_lm_sensors(data: dict, now: datetime) -> list[SensorReading]:
    readings: list[SensorReading] = []
    for chip, features in data.items():
        if not isinstance(features, dict):
            continue
        for feature, values in features.items():
            if not isinstance(values, dict):
                continue
            temp = next((v for k, v in values.items() if k.endswith("_input")), None)
            if temp is None:
                continue
            readings.append(
                SensorReading(
                    component_id=f"cpu:{chip}:{feature}",
                    kind=KIND_CPU,
                    label=feature,
                    temperature_c=float(temp),
                    taken_at=now,
                )
            )
    return readings


def read_cpu_sync(now: datetime) -> list[SensorReading]:
    try:
        import psutil

        data = psutil.sensors_temperatures()
        if data:
            readings = parse_psutil_temps(data, now)
            if readings:
                return readings
    except (ImportError, AttributeError, OSError) as exc:
        log.warning("psutil.sensors_temperatures недоступен: %s", exc)

    sensors_bin = shutil.which("sensors")
    if sensors_bin:
        try:
            out = subprocess.run(
                [sensors_bin, "-j"],
                capture_output=True,
                text=True,
                timeout=SENSORS_TIMEOUT_S,
                check=True,
            )
            return parse_lm_sensors(json.loads(out.stdout), now)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            log.warning("fallback `sensors -j` не сработал: %s", exc)

    log.warning("Не удалось получить температуру CPU ни одним способом")
    return []
