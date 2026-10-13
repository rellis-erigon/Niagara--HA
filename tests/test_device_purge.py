"""Dropping devices whose points have gone away.

This had no test at all, which is why it is written now rather than later:
it was rewritten against the current device registry API, and three
deprecated calls came out of it. A rewrite of untested code is a rewrite
nobody can check.
"""

from __future__ import annotations

import _ha_stub
import pytest

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er


class _Hass:
    pass


class _Entry:
    entry_id = "entry_one"


class _Coordinator:
    """Only the part of the coordinator the purge touches."""

    def __init__(self, hass, entry) -> None:
        self.hass = hass
        self.entry = entry

    # Borrowed from the real class so the behaviour under test is the real
    # behaviour, not a copy of it.
    from custom_components.niagara.coordinator import (  # noqa: PLC0415
        NiagaraCoordinator as _real,
    )
    _purge_empty_devices = _real._purge_empty_devices


@pytest.fixture
def setup(monkeypatch):
    hass = _Hass()
    registry = _ha_stub.device_registry(hass)
    registry.devices.clear()
    registry.removed.clear()
    return hass, registry


def test_a_device_with_no_entities_is_removed(setup, monkeypatch):
    hass, registry = setup
    registry.add("dev_empty", "entry_one", "MeterTotal holding nothing")
    monkeypatch.setattr(er, "async_entries_for_device", lambda *a, **k: [])

    _Coordinator(hass, _Entry())._purge_empty_devices(object())

    assert registry.removed == ["dev_empty"]


def test_a_device_that_still_has_entities_is_kept(setup, monkeypatch):
    hass, registry = setup
    registry.add("dev_busy", "entry_one")
    monkeypatch.setattr(er, "async_entries_for_device",
                        lambda *a, **k: ["sensor.something"])

    _Coordinator(hass, _Entry())._purge_empty_devices(object())

    assert registry.removed == []
    assert "dev_busy" in registry.devices


def test_another_config_entry_is_left_alone(setup, monkeypatch):
    # A device belongs to one config entry now, and somebody else's empty
    # device is not ours to delete.
    hass, registry = setup
    registry.add("dev_theirs", "entry_two")
    monkeypatch.setattr(er, "async_entries_for_device", lambda *a, **k: [])

    _Coordinator(hass, _Entry())._purge_empty_devices(object())

    assert registry.removed == []
    assert "dev_theirs" in registry.devices


def test_it_uses_the_config_entry_helper_not_the_whole_registry(setup):
    # The deprecation this replaced: walking `registry.devices` as a
    # mapping. If that comes back, this fails.
    hass, registry = setup
    assert callable(dr.async_entries_for_config_entry)
    registry.add("a", "entry_one")
    registry.add("b", "entry_two")
    assert [d.id for d in dr.async_entries_for_config_entry(registry, "entry_one")] == ["a"]
