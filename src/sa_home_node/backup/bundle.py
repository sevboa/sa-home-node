from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

REMOTE_NODECTL = "~/.local/bin/nodectl"
META_KEYS = ("format", "node", "identity_label", "snapshot_label", "identity_meta", "snapshot_meta")


class RestoreError(Exception):
    pass


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.chmod(path, 0o600)


def _dump(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")


def write_dir(directory: Path, bundle: dict[str, Any], summary: str) -> list[Path]:
    directory = Path(directory).expanduser()
    if directory.exists() and any(directory.iterdir()):
        raise RestoreError(f"каталог {directory} не пуст — укажите новый или пустой")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    files = {"identity.json": _dump(bundle["identity"]) + b"\n"}
    if bundle.get("snapshot") is not None:
        files["snapshot.json"] = _dump(bundle["snapshot"]) + b"\n"
    files["meta.json"] = _dump({k: bundle.get(k) for k in META_KEYS}) + b"\n"
    files["summary.txt"] = (summary + "\n").encode("utf-8")
    written = []
    for name, data in files.items():
        _write_private(directory / name, data)
        written.append(directory / name)
    return written


def bundle_to_bytes(bundle: dict[str, Any]) -> bytes:
    return _dump(bundle)


def load_bundle(path: Path, bundle_format: str) -> dict[str, Any]:
    path = Path(path).expanduser()
    try:
        if path.is_dir():
            meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
            bundle = {**meta, "identity": json.loads((path / "identity.json").read_text("utf-8"))}
            snap = path / "snapshot.json"
            bundle["snapshot"] = json.loads(snap.read_text("utf-8")) if snap.exists() else None
        else:
            bundle = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError) as exc:
        raise RestoreError(f"не удалось прочитать бандл {path}: {exc}") from exc
    if bundle.get("format") != bundle_format:
        raise RestoreError(f"неизвестный формат бандла: {bundle.get('format')!r}")
    return bundle


def stage_over_ssh(host: str, payload: bytes, remote_nodectl: str = REMOTE_NODECTL) -> None:
    cmd = f"{remote_nodectl} backup stage"
    res = subprocess.run(["ssh", host, cmd], input=payload, capture_output=True, check=False)
    if res.returncode != 0:
        raise RestoreError(
            f"ssh {host}: backup stage завершился кодом {res.returncode}: "
            f"{res.stderr.decode(errors='replace').strip()}"
        )


def apply_over_ssh(host: str, remote_nodectl: str = REMOTE_NODECTL, extra: str = "") -> int:
    cmd = f"{remote_nodectl} backup apply --yes {extra}".strip()
    return subprocess.run(["ssh", "-t", host, cmd], check=False).returncode
