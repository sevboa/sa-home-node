from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta
from math import sqrt
from typing import Any

from sa_home_node.db import Database
from sa_home_node.monitor.hostmodels import HostMetricReading
from sa_home_node.monitor.models import (
    BaselineStats,
    DiskSummary,
    SensorReading,
    SmartSnapshot,
)

DISK_SUMMARIES_KEY = "disk_summaries_cache"


class DataStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def _fetch_all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        return await self.db.fetch_all(sql, params)

    async def get_state(self, key: str) -> str | None:
        row = await self.db.fetch_one("SELECT value FROM app_state WHERE key=?", (key,))
        return row["value"] if row else None

    async def set_state(self, key: str, value: str) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "INSERT INTO app_state(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    async def start_job_run(self, job_type: str, started_at: datetime) -> int:
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "INSERT INTO job_runs(job_type, started_at, status) VALUES(?, ?, 'running')",
                (job_type, started_at.isoformat()),
            )
            return cur.lastrowid

    async def finish_job_run(
        self,
        run_id: int,
        status: str,
        finished_at: datetime,
        error: str | None = None,
        metrics_json: str | None = None,
    ) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE job_runs SET status=?, finished_at=?, error=?, metrics_json=? WHERE id=?",
                (status, finished_at.isoformat(), error, metrics_json, run_id),
            )

    async def recent_job_runs(self, limit: int = 10) -> list[dict]:
        return await self._fetch_all("SELECT * FROM job_runs ORDER BY id DESC LIMIT ?", (limit,))

    async def job_run_counts(self) -> dict[str, int]:
        rows = await self._fetch_all("SELECT status, COUNT(*) AS n FROM job_runs GROUP BY status")
        return {r["status"]: r["n"] for r in rows}

    async def prune_job_runs(self, keep_last: int = 500) -> int:
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "DELETE FROM job_runs WHERE id NOT IN "
                "(SELECT id FROM job_runs ORDER BY id DESC LIMIT ?)",
                (keep_last,),
            )
            return cur.rowcount

    async def record_readings(self, readings: list[SensorReading]) -> None:
        await self._insert_readings(
            "readings(component_id, temperature_c, taken_at)",
            [(r.component_id, r.temperature_c, r.taken_at.isoformat()) for r in readings],
        )

    async def record_host_readings(self, readings: list[HostMetricReading]) -> None:
        await self._insert_readings(
            "host_readings(component_id, metric, value, taken_at)",
            [(r.component_id, r.metric, r.value, r.taken_at.isoformat()) for r in readings],
        )

    async def prune_readings(self, keep_per_component: int) -> int:
        return await self._prune_keep_last("readings", keep_per_component)

    async def prune_host_readings(self, keep_per_component: int) -> int:
        return await self._prune_keep_last("host_readings", keep_per_component)

    async def _insert_readings(self, target: str, rows: list[tuple]) -> None:
        if not rows:
            return
        marks = ", ".join("?" for _ in rows[0])
        async with self.db.transaction() as conn:
            await conn.executemany(f"INSERT INTO {target} VALUES({marks})", rows)

    async def _prune_keep_last(self, table: str, keep_per_component: int) -> int:
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                f"DELETE FROM {table} WHERE id IN ("
                "  SELECT id FROM ("
                "    SELECT id, ROW_NUMBER() OVER ("
                "      PARTITION BY component_id ORDER BY id DESC"
                f"    ) AS rn FROM {table}"
                "  ) WHERE rn > ?"
                ")",
                (keep_per_component,),
            )
            return cur.rowcount

    async def host_trend(self, metric: str, hours: int, now: datetime) -> list[dict]:
        cutoff = (now - timedelta(hours=int(hours))).isoformat()
        rows = await self._fetch_all(
            "SELECT substr(taken_at, 1, 13) || ':' || "
            "  printf('%02d', (CAST(substr(taken_at, 15, 2) AS INTEGER) / 10) * 10) AS bucket, "
            "  AVG(value) AS avg_value, MAX(value) AS max_value, COUNT(*) AS n "
            "FROM host_readings "
            "WHERE component_id = ? AND taken_at >= ? "
            "GROUP BY bucket ORDER BY bucket",
            (f"host:{metric}", cutoff),
        )
        return [
            {
                "bucket": r["bucket"],
                "avg": round(r["avg_value"], 2),
                "max": round(r["max_value"], 2),
                "samples": r["n"],
            }
            for r in rows
        ]

    async def baseline_stats(self, component_id: str, window: int) -> BaselineStats:
        row = await self.db.fetch_one(
            "SELECT COUNT(*) AS n, AVG(t) AS m, AVG(t*t) AS mm FROM ("
            "  SELECT temperature_c AS t FROM readings WHERE component_id=? "
            "  ORDER BY id DESC LIMIT ?"
            ")",
            (component_id, window),
        )
        n = row["n"] or 0
        if n == 0:
            return BaselineStats(count=0, mean=0.0, std=0.0)
        mean = row["m"]
        variance = max(0.0, (row["mm"] or 0.0) - mean * mean)
        return BaselineStats(count=n, mean=mean, std=sqrt(variance))

    async def get_smart_snapshot(self, component_id: str) -> SmartSnapshot | None:
        row = await self.db.fetch_one(
            "SELECT * FROM smart_snapshots WHERE component_id=?", (component_id,)
        )
        if row is None:
            return None
        return SmartSnapshot(
            component_id=row["component_id"],
            label=row["label"],
            health=row["health"],
            attrs={int(k): int(v) for k, v in json.loads(row["attrs_json"]).items()},
            taken_at=datetime.fromisoformat(row["taken_at"]),
        )

    async def get_smart_health_map(self) -> dict[str, str | None]:
        rows = await self._fetch_all("SELECT component_id, health FROM smart_snapshots")
        return {r["component_id"].removeprefix("disk:"): r["health"] for r in rows}

    async def save_smart_snapshot(self, snap: SmartSnapshot) -> None:
        attrs_json = json.dumps({str(k): v for k, v in snap.attrs.items()})
        async with self.db.transaction() as conn:
            await conn.execute(
                "INSERT INTO smart_snapshots(component_id, label, health, attrs_json, taken_at) "
                "VALUES(?, ?, ?, ?, ?) "
                "ON CONFLICT(component_id) DO UPDATE SET "
                "label=excluded.label, health=excluded.health, "
                "attrs_json=excluded.attrs_json, taken_at=excluded.taken_at",
                (snap.component_id, snap.label, snap.health, attrs_json, snap.taken_at.isoformat()),
            )

    async def save_disk_summaries(self, disks: list[DiskSummary]) -> None:
        await self.set_state(DISK_SUMMARIES_KEY, json.dumps([asdict(d) for d in disks]))

    async def get_disk_summaries(self) -> list[DiskSummary] | None:
        raw = await self.get_state(DISK_SUMMARIES_KEY)
        if raw is None:
            return None
        return [DiskSummary(**d) for d in json.loads(raw)]
