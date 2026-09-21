"""Tests for boolean interpretation.

is_on previously returned False for anything outside its true-list, so an
unrecognised state reported a confident "off" — actively misleading for an
alarm or a running status.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.niagara.binary_sensor import (  # noqa: E402
    FALSE_VALUES,
    NAME_PATTERNS_BINARY,
    TRUE_VALUES,
)


def read(value):
    """The interpretation is_on applies to a raw Niagara value."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    return None


@pytest.mark.parametrize("value", ["true", "1", "on", "active", "enabled", "TRUE", " On "])
def test_true_shapes(value):
    assert read(value) is True


@pytest.mark.parametrize("value", ["false", "0", "off", "inactive", "disabled", "OFF"])
def test_false_shapes(value):
    assert read(value) is False


@pytest.mark.parametrize("value", ["", "unknown", "fault", "null", "2", "OK {ok}"])
def test_unrecognised_is_none_not_false(value):
    assert read(value) is None


def test_missing_value_is_none():
    assert read(None) is None


def test_true_and_false_sets_do_not_overlap():
    assert not TRUE_VALUES & FALSE_VALUES


def test_run_does_not_match_inside_unrelated_words():
    """"run" used to match any word containing those letters."""
    running = next(p for p, dc in NAME_PATTERNS_BINARY if "run" in p.pattern)
    assert running.search("Pump Run Status")
    assert not running.search("Brunswick Sensor")
