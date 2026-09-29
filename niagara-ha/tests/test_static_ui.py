"""Invariants in the single-page UI that Python tests can still hold.

There is no browser here, so these are deliberately narrow: they guard
the couple of places where a wrong selector silently breaks a save rather
than throwing anything anyone would see.
"""
import re
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[1] / "static" / "index.html"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text()


def test_slot_bindings_are_harvested_only_from_slot_controls(page):
    """`slot-select` is a styling class shared by inputs across several
    dialogs. Harvesting all of them posted the faceplate picker as a
    binding named "undefined", the add-on rejected the whole request, and
    nothing saved — with a toast that did not say why."""
    harvests = re.findall(
        r"querySelectorAll\('(\.slot-select[^']*)'\)\.forEach\(sel => \{\s*"
        r"bindings\[", page)
    assert harvests, "the binding harvest was renamed; update this test"
    for selector in harvests:
        assert "[data-slot]" in selector, (
            f"{selector} picks up controls that are not slots")


def test_the_faceplate_picker_is_not_a_slot(page):
    """It must not carry data-slot, or it becomes a binding again."""
    picker = re.search(r"<select[^>]*id=\"faceplate-select\"[^>]*>", page)
    assert picker, "the faceplate picker is gone"
    assert "data-slot" not in picker.group(0)


def test_api_failures_carry_the_server_reason(page):
    """The API says why it refused. Throwing the status text alone turned
    every failure into the caller's generic message."""
    helper = re.search(r"async function api\(path, opts\) \{.*?\n\}", page, re.S)
    assert helper, "the api helper was renamed; update this test"
    assert "body.error" in helper.group(0)
