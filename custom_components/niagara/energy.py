"""Put published meters on the energy dashboard without anyone asking.

Getting a meter onto that dashboard by hand is a genuinely obscure sequence:
the sensor needs the right device class, the right state class and a unit
Home Assistant can reconcile, and only then does it appear in a picker that
says nothing about why anything is missing. Having gone to the trouble of
typing a device as a meter and publishing it, being made to do that again in
another screen is a poor reward.

So publishing is the trigger. This only ever adds: a source someone removed
deliberately is not put back, and nothing already configured is touched.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Slots whose meaning on the energy dashboard is unambiguous. Anything else
# is left alone — a guess here shows up as a wrong number on the one screen
# people trust for their bills.
GRID_SLOTS = frozenset({"energy_total", "energy", "energy_imported"})
SOLAR_SLOTS = frozenset({"energy_generated"})
WATER_SLOTS = frozenset({"volume_total", "water_total"})
GAS_SLOTS = frozenset({"gas_total", "volume_gas"})


def _configured_entities(prefs: dict) -> set[str]:
    """Every entity id already named anywhere in the energy preferences."""
    found: set[str] = set()
    for source in prefs.get("energy_sources", []):
        for key in ("stat_energy_from", "stat_energy_to", "stat_cost",
                    "stat_compensation"):
            if source.get(key):
                found.add(source[key])
        for flow_key in ("flow_from", "flow_to"):
            for flow in source.get(flow_key, []) or []:
                for key in ("stat_energy_from", "stat_energy_to", "stat_cost"):
                    if flow.get(key):
                        found.add(flow[key])
    for device in prefs.get("device_consumption", []) or []:
        if device.get("stat_consumption"):
            found.add(device["stat_consumption"])
    return found


def plan_additions(
    prefs: dict, meters: list[dict],
) -> tuple[dict, list[str]]:
    """Work out what to add. Returns (new prefs, what changed).

    Split out from the async plumbing so the decisions can be tested without
    a running Home Assistant, which is where the risk actually lives.
    """
    prefs = {
        "energy_sources": list(prefs.get("energy_sources", []) or []),
        "device_consumption": list(prefs.get("device_consumption", []) or []),
        **{k: v for k, v in prefs.items()
           if k not in ("energy_sources", "device_consumption")},
    }
    existing = _configured_entities(prefs)
    changes: list[str] = []

    grid = next(
        (s for s in prefs["energy_sources"] if s.get("type") == "grid"), None
    )

    for meter in meters:
        entity_id = meter.get("entity_id")
        slot = meter.get("slot") or ""
        if not entity_id or entity_id in existing:
            continue

        if slot in SOLAR_SLOTS:
            prefs["energy_sources"].append({
                "type": "solar", "stat_energy_from": entity_id,
                "config_entry_solar_forecast": None,
            })
            changes.append(f"{entity_id} as solar production")
        elif slot in WATER_SLOTS:
            prefs["energy_sources"].append({
                "type": "water", "stat_energy_from": entity_id,
                "stat_cost": None, "entity_energy_price": None,
                "number_energy_price": None,
            })
            changes.append(f"{entity_id} as water")
        elif slot in GAS_SLOTS:
            prefs["energy_sources"].append({
                "type": "gas", "stat_energy_from": entity_id,
                "stat_cost": None, "entity_energy_price": None,
                "number_energy_price": None,
            })
            changes.append(f"{entity_id} as gas")
        elif slot in GRID_SLOTS:
            # A building has many submeters and one supply. Treating every
            # board as grid import would count the same electricity several
            # times over, so submeters go in as individual devices and the
            # grid source is left to whoever knows which meter is the main.
            prefs["device_consumption"].append({
                "stat_consumption": entity_id,
                "name": meter.get("name") or entity_id,
            })
            changes.append(f"{entity_id} as an individual device")
        else:
            continue
        existing.add(entity_id)

    if grid is None and not any(
        s.get("type") == "grid" for s in prefs["energy_sources"]
    ):
        # The dashboard refuses to render without a grid source, even one
        # with no flows, and an empty dashboard reads as "this did not work".
        prefs["energy_sources"].append({
            "type": "grid", "flow_from": [], "flow_to": [],
            "cost_adjustment_day": 0.0,
        })
        changes.append("an empty grid source so the dashboard renders")

    return prefs, changes


async def async_add_published_meters(
    hass: "HomeAssistant", meters: list[dict],
) -> list[str]:
    """Add any published meter that is not already on the dashboard."""
    if not meters:
        return []
    try:
        from homeassistant.components.energy import data as energy_data
    except ImportError:
        _LOGGER.debug("Energy integration is not available")
        return []

    try:
        manager = await energy_data.async_get_manager(hass)
    except Exception as err:  # noqa: BLE001 - never break a refresh over this
        _LOGGER.debug("Could not read energy preferences: %s", err)
        return []

    current = dict(manager.data or {})
    if not current:
        current = {"energy_sources": [], "device_consumption": []}

    updated, changes = plan_additions(current, meters)
    if not changes:
        return []

    try:
        await manager.async_update(updated)
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Could not update the energy dashboard: %s", err)
        return []

    _LOGGER.info(
        "Added %d meter(s) to the energy dashboard: %s",
        len(changes), "; ".join(changes),
    )
    return changes
