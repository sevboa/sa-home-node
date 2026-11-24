CREATE TABLE IF NOT EXISTS app_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outbox (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id           INTEGER NOT NULL,
    kind              TEXT NOT NULL,
    payload_json      TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    attempts          INTEGER NOT NULL DEFAULT 0,
    dedup_key         TEXT,
    expires_at        TEXT NOT NULL,
    message_thread_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_outbox_created ON outbox(created_at, id);
CREATE INDEX IF NOT EXISTS idx_outbox_dedup ON outbox(chat_id, dedup_key);

CREATE TABLE IF NOT EXISTS invites (
    code                TEXT PRIMARY KEY,
    issued_by_chat_id   INTEGER NOT NULL,
    issued_by_user_id   INTEGER,
    created_at          TEXT NOT NULL,
    expires_at          TEXT NOT NULL,
    redeemed_at         TEXT,
    redeemed_chat_id    INTEGER,
    redeemed_user_id    INTEGER,
    redeemed_user_name  TEXT,
    revoked_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_invites_open ON invites(expires_at) WHERE redeemed_at IS NULL AND revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS swarm_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT NOT NULL,
    node        TEXT,
    text        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_swarm_events_created ON swarm_events(created_at);

CREATE TABLE IF NOT EXISTS health_notifications (
    component_id  TEXT NOT NULL,
    chat_id       INTEGER NOT NULL,
    kind          TEXT NOT NULL,
    message_id    INTEGER,
    sent_at       TEXT NOT NULL,
    PRIMARY KEY (component_id, chat_id, kind)
);
