import tomllib
from pathlib import Path

import sa_home_node

ROOT = Path(__file__).resolve().parents[1]


def test_version_is_set():
    assert sa_home_node.__version__


def test_data_files_are_packaged():
    package = Path(sa_home_node.__file__).parent
    globs = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"][
        "package-data"
    ]["sa_home_node"]
    data = {p for p in package.rglob("*") if p.is_file() and p.suffix not in (".py", ".pyc")}
    covered = {p for pattern in globs for p in package.glob(pattern)}
    assert data <= covered
