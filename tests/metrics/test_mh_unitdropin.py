from __future__ import annotations

from pathlib import Path

import pytest

from sa_home_node.metrics.unitdropin import DROPIN_NAME, overlay_dropin_content

FIXTURES = Path(__file__).parent.parent / "fixtures" / "metrics"


@pytest.mark.parametrize(
    ("unit", "fixture"),
    [("overlay.service", "dropin.conf"), ("", "dropin-empty.conf")],
    ids=["with-unit", "empty-unit"],
)
def test_dropin_matches_fixture(unit, fixture):
    expected = (FIXTURES / "fixups" / fixture).read_text(encoding="utf-8")
    assert overlay_dropin_content(unit) == expected


def test_dropin_name():
    assert DROPIN_NAME == "50-sa-home-overlay.conf"
