from __future__ import annotations

import base64
import binascii
import hashlib
import json
import tomllib
from typing import Any

from sa_home_node.backup import sealed
from sa_home_node.backup.spec import IdentityError


def canonical_bytes(doc: dict[str, Any]) -> bytes:
    return json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def plain_hash(plaintext: bytes) -> str:
    return "sha256:" + hashlib.sha256(plaintext).hexdigest()


def render_package(format: str, source: str, blob: bytes) -> bytes:
    text = (
        f'format = "{format}"\n'
        f"source = {json.dumps(source)}\n"
        f'sealed = "{base64.b64encode(blob).decode("ascii")}"\n'
    )
    return text.encode("utf-8")


def parse_package(format: str, data: bytes) -> tuple[str, bytes]:
    try:
        doc = tomllib.loads(data.decode("utf-8"))
        if doc.get("format") != format:
            raise IdentityError(f"неизвестный формат пакета: {doc.get('format')!r}")
        return str(doc["source"]), base64.b64decode(doc["sealed"], validate=True)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, KeyError, binascii.Error) as exc:
        raise IdentityError(f"пакет identity повреждён: {exc}") from exc


def open_identity(private_key: bytes, blob: bytes) -> dict[str, Any]:
    try:
        return json.loads(sealed.open_sealed(private_key, blob))
    except (sealed.SealedError, ValueError) as exc:
        raise IdentityError(f"не удалось расшифровать identity: {exc}") from exc
