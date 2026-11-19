import base64
import hashlib

import pytest

from sa_home_node.proto.chunks import CHUNK_BYTES, download_chunks
from sa_home_node.proto.wire import Address


class ChunkLink:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[tuple[dict, Address]] = []

    async def command(self, action, args=None, dst=None, *, timeout=None):
        self.calls.append((args, dst))
        offset, length = args["offset"], args["length"]
        data = self.payload[offset : offset + length]
        return {
            "data_b64": base64.b64encode(data).decode(),
            "eof": offset + len(data) >= len(self.payload),
        }


async def test_reassembles_and_passes_base_args() -> None:
    payload = bytes(range(256)) * (CHUNK_BYTES // 256 + 100)
    link = ChunkLink(payload)
    dst = Address(node="node-a", service="svc")
    got = await download_chunks(
        link,
        "chunk",
        dst,
        base_args={"session_id": "abc"},
        expected_sha256=hashlib.sha256(payload).hexdigest(),
    )
    assert got == payload
    assert [args for args, _ in link.calls] == [
        {"session_id": "abc", "offset": 0, "length": CHUNK_BYTES},
        {"session_id": "abc", "offset": CHUNK_BYTES, "length": CHUNK_BYTES},
    ]
    assert all(call_dst is dst for _, call_dst in link.calls)


async def test_rejects_wrong_hash() -> None:
    with pytest.raises(ValueError, match="своя ошибка"):
        await download_chunks(
            ChunkLink(b"data"),
            "chunk",
            Address(),
            expected_sha256="0" * 64,
            integrity_error="своя ошибка",
        )


async def test_empty_response_ends_download() -> None:
    assert await download_chunks(ChunkLink(b""), "chunk", Address()) == b""
