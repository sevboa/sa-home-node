from __future__ import annotations

import pytest

from sa_home_node.backup import package, sealed
from sa_home_node.backup.spec import IdentityError

FORMAT = "fmt/identity/1"


def test_canonical_and_hash():
    a = package.canonical_bytes({"b": 1, "a": "я"})
    assert a == '{"a":"я","b":1}'.encode()
    assert package.canonical_bytes({"a": "я", "b": 1}) == a
    assert package.plain_hash(a).startswith("sha256:")
    assert len(package.plain_hash(a)) == 7 + 64


def test_render_parse():
    blob = bytes(range(40))
    data = package.render_package(FORMAT, "node-a", blob)
    assert data.decode().splitlines()[0] == f'format = "{FORMAT}"'
    assert len(data.decode().splitlines()) == 3
    assert package.parse_package(FORMAT, data) == ("node-a", blob)


@pytest.mark.parametrize(
    "data",
    [
        b"\xff\xfe",
        b"format = [",
        b'format = "other"\nsource = "a"\nsealed = ""\n',
        f'format = "{FORMAT}"\nsource = "a"\n'.encode(),
        f'format = "{FORMAT}"\nsource = "a"\nsealed = "!!"\n'.encode(),
    ],
)
def test_parse_errors(data):
    with pytest.raises(IdentityError):
        package.parse_package(FORMAT, data)


def test_open_identity():
    private, public = sealed.generate_keypair()
    blob = sealed.seal(public, package.canonical_bytes({"k": 1}))
    assert package.open_identity(private, blob) == {"k": 1}
    other, _ = sealed.generate_keypair()
    with pytest.raises(IdentityError, match="не удалось расшифровать identity"):
        package.open_identity(other, blob)
    with pytest.raises(IdentityError):
        package.open_identity(private, sealed.seal(public, b"not json"))
