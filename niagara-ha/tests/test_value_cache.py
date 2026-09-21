"""Tests for the value cache format, migration and pruning.

The cache previously held {path: value} with no timestamp and was never
pruned. A reading frozen since the last failed poll was indistinguishable
from a live one, and entries for disabled or renamed points accumulated
forever — this deployment reached 3,858 entries for 1,912 enabled points.
"""
import json
import sys
import types
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))


@pytest.fixture
def main_module(tmp_path, monkeypatch):
    """Import main.py with its Niagara dependencies stubbed out."""
    for name, attrs in (
        ("obix_client", {"ObixClient": object, "ObixError": Exception}),
        ("point_manager", {
            "DEVICE_FOLDERS_FILE": tmp_path / "folders.yaml",
            "POINTS_DIR": tmp_path,
            "POINTS_FILE": tmp_path / "points.yaml",
            "filter_enabled": None,
            "load_auto_enable_rules": None,
            "load_device_folders": None,
            "load_point_selections": None,
            "save_point_selections": None,
        }),
    ):
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, name, module)

    sys.modules.pop("main", None)
    import main

    monkeypatch.setattr(main, "VALUES_FILE", tmp_path / "values.json")
    return main


def test_flat_cache_is_migrated(main_module, tmp_path):
    """Old {path: value} entries become timestamped, marked not-yet-polled."""
    (tmp_path / "values.json").write_text(json.dumps({"/a/": "21.5", "/b/": "true"}))

    cache = main_module._load_values_cache()

    assert cache["/a/"] == {"value": "21.5", "status": "unknown", "ts": 0.0}
    assert cache["/b/"]["value"] == "true"
    # ts 0.0 reads as ancient, so a migrated value is stale until truly polled.
    assert cache["/a/"]["ts"] == 0.0


def test_rich_entries_pass_through(main_module, tmp_path):
    entry = {"value": "12.0", "status": "ok", "ts": 1750000000.0}
    (tmp_path / "values.json").write_text(json.dumps({"/a/": entry}))

    assert main_module._load_values_cache()["/a/"] == entry


def test_mixed_cache_is_normalised(main_module, tmp_path):
    (tmp_path / "values.json").write_text(json.dumps({
        "/rich/": {"value": "1", "status": "ok", "ts": 100.0},
        "/flat/": "2",
    }))

    cache = main_module._load_values_cache()

    assert all("value" in e and "ts" in e for e in cache.values())
    assert cache["/flat/"]["status"] == "unknown"


@pytest.mark.parametrize("payload", ["not a dict", "[]", "{broken", ""])
def test_unreadable_cache_yields_empty(main_module, tmp_path, payload):
    (tmp_path / "values.json").write_text(payload)
    assert main_module._load_values_cache() == {}


def test_missing_cache_yields_empty(main_module):
    assert main_module._load_values_cache() == {}


def test_prune_drops_inactive_paths(main_module):
    cache = {
        "/active/": {"value": "1", "status": "ok", "ts": 1.0},
        "/disabled/": {"value": "2", "status": "ok", "ts": 1.0},
        "/renamed/x/x": {"value": "3", "status": "ok", "ts": 1.0},
    }

    removed = main_module._prune_values_cache(cache, {"/active/"})

    assert removed == 2
    assert set(cache) == {"/active/"}


def test_prune_is_a_no_op_when_all_active(main_module):
    cache = {"/a/": {"value": "1", "status": "ok", "ts": 1.0}}
    assert main_module._prune_values_cache(cache, {"/a/"}) == 0
    assert set(cache) == {"/a/"}


def test_round_trip_through_disk(main_module, tmp_path):
    cache = {"/a/": {"value": "21.5", "status": "ok", "ts": 1750000000.0}}
    main_module._write_values_cache(cache)
    assert main_module._load_values_cache() == cache
