from __future__ import annotations

from sa_home_node.db import Database

SCHEMA = """
CREATE TABLE IF NOT EXISTS job_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type     TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL DEFAULT 'running',
    error        TEXT,
    metrics_json TEXT
);

CREATE TABLE IF NOT EXISTS app_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS health_states (
    component_id          TEXT PRIMARY KEY,
    kind                  TEXT NOT NULL,
    label                 TEXT NOT NULL,
    status                TEXT NOT NULL,
    last_temperature_c    REAL,
    consecutive_count     INTEGER NOT NULL DEFAULT 0,
    alerting_since        TEXT,
    first_seen_at         TEXT NOT NULL,
    last_seen_at          TEXT NOT NULL,
    notified_alert_at     TEXT,
    notified_cleared_at   TEXT
);

CREATE TABLE IF NOT EXISTS readings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    component_id  TEXT NOT NULL,
    temperature_c REAL NOT NULL,
    taken_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_readings_component ON readings(component_id, id);

CREATE TABLE IF NOT EXISTS host_metric_states (
    component_id         TEXT PRIMARY KEY,
    metric               TEXT NOT NULL,
    label                TEXT NOT NULL,
    unit                 TEXT NOT NULL DEFAULT '',
    status               TEXT NOT NULL,
    last_value           REAL,
    consecutive_count    INTEGER NOT NULL DEFAULT 0,
    alerting_since       TEXT,
    first_seen_at        TEXT NOT NULL,
    last_seen_at         TEXT NOT NULL,
    notified_alert_at    TEXT,
    notified_cleared_at  TEXT
);

CREATE TABLE IF NOT EXISTS host_readings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    component_id  TEXT NOT NULL,
    metric        TEXT NOT NULL,
    value         REAL NOT NULL,
    taken_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_host_readings_metric ON host_readings(component_id, taken_at);

CREATE TABLE IF NOT EXISTS smart_snapshots (
    component_id  TEXT PRIMARY KEY,
    label         TEXT NOT NULL,
    health        TEXT,
    attrs_json    TEXT NOT NULL,
    taken_at      TEXT NOT NULL
);
"""


async def apply_schema(db: Database) -> None:
    await db.conn.executescript(SCHEMA)
    await db.conn.commit()
