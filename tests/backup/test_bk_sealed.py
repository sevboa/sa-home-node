from __future__ import annotations

import stat

import pytest

from sa_home_node.backup import sealed
from sa_home_node.backup.sealed import SealedError


def test_round_trip():
    priv, pub = sealed.generate_keypair()
    for data in (b"", b"secret key", bytes(range(256)) * 100):
        blob = sealed.seal(pub, data)
        assert blob.startswith(sealed.MAGIC)
        assert sealed.open_sealed(priv, blob) == data


def test_each_seal_differs():
    _, pub = sealed.generate_keypair()
    assert sealed.seal(pub, b"x") != sealed.seal(pub, b"x")


def test_wrong_key():
    _, pub = sealed.generate_keypair()
    other_priv, _ = sealed.generate_keypair()
    with pytest.raises(SealedError, match="чужой ключ"):
        sealed.open_sealed(other_priv, sealed.seal(pub, b"x"))


def test_layout():
    _, pub = sealed.generate_keypair()
    blob = sealed.seal(pub, b"abc")
    assert len(blob) == 5 + 32 + 12 + 3 + 16


@pytest.mark.parametrize("offset", [6, 38, -1])
def test_corruption_every_region(offset):
    priv, pub = sealed.generate_keypair()
    blob = bytearray(sealed.seal(pub, b"payload"))
    blob[offset] ^= 1
    with pytest.raises(SealedError):
        sealed.open_sealed(priv, bytes(blob))


def test_bad_magic_version_truncated():
    priv, pub = sealed.generate_keypair()
    blob = sealed.seal(pub, b"x")
    with pytest.raises(SealedError, match="неверная магия"):
        sealed.open_sealed(priv, b"XXXXX" + blob[5:])
    with pytest.raises(SealedError, match="неизвестная версия формата: b'2'"):
        sealed.open_sealed(priv, b"SAHB2" + blob[5:])
    with pytest.raises(SealedError, match="блоб обрезан"):
        sealed.open_sealed(priv, blob[:20])
    with pytest.raises(SealedError, match="неверная магия"):
        sealed.open_sealed(priv, b"")


def test_small_order_point_rejected():
    priv, _ = sealed.generate_keypair()
    with pytest.raises(SealedError, match="непригодный публичный ключ получателя"):
        sealed.seal(bytes(32), b"x")
    blob = sealed.MAGIC + bytes(32) + bytes(12) + bytes(16)
    with pytest.raises(SealedError, match="не удалось расшифровать"):
        sealed.open_sealed(priv, blob)


def test_key_dump_load():
    priv, pub = sealed.generate_keypair()
    text = sealed.dump_key(pub)
    assert len(text) == 44
    assert sealed.load_key(text + "\n") == pub
    assert sealed.public_from_private(priv) == pub
    for bad in ("", "!!!", text[:-4]):
        with pytest.raises(SealedError):
            sealed.load_key(bad)
    with pytest.raises(SealedError, match="ключ должен быть 32 байт, а не 5"):
        sealed.dump_key(b"short")
    with pytest.raises(SealedError, match="некорректный публичный ключ"):
        sealed.seal(b"short", b"x")


def test_private_key_file(tmp_path):
    priv, _ = sealed.generate_keypair()
    path = tmp_path / "sub" / "k"
    sealed.write_private_key(path, priv)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert len(path.read_text()) == 45
    assert sealed.read_private_key(path) == priv
    with pytest.raises(FileExistsError):
        sealed.write_private_key(path, priv)
    assert sealed.read_private_key(path) == priv
    with pytest.raises(SealedError, match="не удалось прочитать файл ключа"):
        sealed.read_private_key(tmp_path / "missing")
