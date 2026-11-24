from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

PUBLISH_MARKER = "backup-publish.ok"


def marker_path(state_path: str | Path) -> Path:
    return Path(state_path).parent / PUBLISH_MARKER


def publish_allowed(state_path: str | Path) -> bool:
    return marker_path(state_path).exists()


def allow_publish(state_path: str | Path, reason: str) -> Path:
    path = marker_path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = f"{datetime.now(tz=UTC).isoformat()} {reason}\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


HOLD_HINT = (
    "публикация бэкапа приостановлена: нет маркера backup-publish.ok. После пересборки "
    "сервера восстановите данные (`sa-home-bot backup restore <нода> --apply`), "
    "для нового сервера или первого деплоя — `nodectl backup-release`"
)
