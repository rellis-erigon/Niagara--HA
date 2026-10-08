"""Services for the Niagara BMS integration.

`niagara.generate_card` turns a typed device into a Lovelace card. The
add-on holds the card definition and knows which point fills each slot, but
only Home Assistant knows the entity ids those points became — so the
substitution has to happen here.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
import yaml
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from .coordinator import NiagaraCoordinator, stable_id

_LOGGER = logging.getLogger(__name__)

def _station_of_path(path: str) -> str:
    """The Niagara station a point path sits under, lowercased."""
    return NiagaraCoordinator._station_of(path)



SERVICE_GENERATE_CARD = "generate_card"
SERVICE_GENERATE_CARDS = "generate_cards"
SERVICE_FIX_STATISTICS = "fix_statistics_units"
SERVICE_RUN_DIAGNOSTICS = "run_diagnostics"
SERVICE_ADD_TO_ENERGY = "add_meters_to_energy"
SERVICE_RESCAN = "rescan"
SERVICE_SYNC_HISTORY = "sync_history"
SERVICE_LEARN_AREAS = "learn_areas"
SERVICE_APPLY_AREAS = "apply_areas"
ATTR_DEVICE = "device"

GENERATE_CARD_SCHEMA = vol.Schema({vol.Required(ATTR_DEVICE): str})

# The platforms a Niagara point can become.
_PLATFORMS = ("sensor", "binary_sensor")


def _entity_id_for_path(
    registry: er.EntityRegistry, path: str,
) -> str | None:
    """Find the entity a point path became, by the unique id we minted."""
    unique_id = f"niagara_{stable_id(path)}"
    for platform in _PLATFORMS:
        entity_id = registry.async_get_entity_id(platform, DOMAIN, unique_id)
        if entity_id:
            return entity_id
    return None


def _substitute(node: Any, mapping: dict[str, str]) -> Any:
    """Replace point paths with entity ids, dropping rows that cannot resolve."""
    if isinstance(node, str):
        return mapping.get(node, node) if node in mapping else node
    if isinstance(node, list):
        out = []
        for item in node:
            resolved = _substitute(item, mapping)
            if resolved is None:
                continue
            out.append(resolved)
        return out
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, item in node.items():
            resolved = _substitute(item, mapping)
            # An `entities` role map that lost every role is an empty map,
            # not None, and a faceplate with nothing bound draws a blank
            # panel rather than an error. Treat empty as unresolved.
            if key in ("entity", "entities") and not resolved:
                return None
            if key == "cards" and not resolved:
                return None
            if resolved is None:
                continue
            out[key] = resolved
        return out
    return node


def _prune_unresolved(node: Any, known: set[str]) -> Any:
    """Drop any row still naming a point path we could not map."""
    if isinstance(node, str):
        return None if node in known else node
    if isinstance(node, list):
        out = []
        for item in node:
            pruned = _prune_unresolved(item, known)
            if pruned is None:
                continue
            out.append(pruned)
        return out
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, item in node.items():
            pruned = _prune_unresolved(item, known)
            # An `entities` role map that lost every role is an empty map,
            # not None, and a faceplate with nothing bound draws a blank
            # panel rather than an error. Treat empty as unresolved.
            if key in ("entity", "entities") and not pruned:
                return None
            if key == "cards" and not pruned:
                return None
            if pruned is None:
                continue
            out[key] = pruned
        return out
    return node


async def _async_register_extra_services(hass: HomeAssistant) -> None:
    """Services that act on Home Assistant rather than on the add-on."""

    async def handle_fix_statistics(call: ServiceCall) -> ServiceResponse:
        from .statistics import async_find_unit_conflicts, async_fix_statistics_units

        if call.data.get("dry_run"):
            conflicts = await async_find_unit_conflicts(hass)
            return {"would_fix": len(conflicts), "conflicts": conflicts}

        fixed = await async_fix_statistics_units(hass)
        return {"fixed": len(fixed), "entities": fixed}

    async def handle_run_diagnostics(call: ServiceCall) -> ServiceResponse:
        entries = hass.data.get(DOMAIN, {})
        if not entries:
            raise HomeAssistantError("Niagara BMS is not set up")
        coordinator = next(iter(entries.values()))

        session = async_get_clientsession(hass)
        try:
            async with session.get(
                f"{coordinator.addon_url}/api/diagnostics",
                timeout=aiohttp.ClientTimeout(total=60),
            ) as response:
                response.raise_for_status()
                return await response.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HomeAssistantError(f"Could not reach the add-on: {err}") from err

    hass.services.async_register(
        DOMAIN, SERVICE_FIX_STATISTICS, handle_fix_statistics,
        schema=vol.Schema({vol.Optional("dry_run", default=False): bool}),
        supports_response=SupportsResponse.ONLY,
    )
    async def handle_rescan(call: ServiceCall) -> ServiceResponse:
        entries = hass.data.get(DOMAIN, {})
        if not entries:
            raise HomeAssistantError("Niagara BMS is not set up")
        coordinator = next(iter(entries.values()))

        session = async_get_clientsession(hass)
        try:
            async with session.post(
                f"{coordinator.addon_url}/api/rescan",
                json={"reason": "requested from Home Assistant"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                response.raise_for_status()
                return await response.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HomeAssistantError(f"Could not reach the add-on: {err}") from err

    hass.services.async_register(
        DOMAIN, SERVICE_RUN_DIAGNOSTICS, handle_run_diagnostics,
        schema=vol.Schema({}),
        supports_response=SupportsResponse.ONLY,
    )
    async def handle_sync_history(call: ServiceCall) -> ServiceResponse:
        """Import the station's trend logs into long-term statistics.

        Available as a service as well as on a timer, because a backfill is
        bounded per run: a station holding years of trend takes several runs
        to catch up, and waiting hours between them is nobody's idea of a
        working feature.
        """
        from .history import HistorySync

        entries = hass.data.get(DOMAIN, {})
        if not entries:
            raise HomeAssistantError("Niagara BMS is not set up")
        coordinator = next(iter(entries.values()))

        syncer = getattr(coordinator, "history_sync", None)
        if syncer is None:
            syncer = HistorySync(hass, coordinator)
            coordinator.history_sync = syncer
        return await syncer.async_run(call.data.get("history"))

    hass.services.async_register(
        DOMAIN, SERVICE_RESCAN, handle_rescan,
        schema=vol.Schema({}),
        supports_response=SupportsResponse.ONLY,
    )
    def _niagara_devices(coordinator):
        """Niagara devices with the folder each one came from.

        A device id is derived from the host and the folder, so the map is
        rebuilt the same way rather than stored — nothing else records
        which folder a registry entry belongs to.
        """
        devices = dr.async_get(hass)
        area_reg = ar.async_get(hass)
        found = {}
        for group in {p.group for p in coordinator.points.values() if p.group}:
            device_id = f"niagara_{stable_id(coordinator.host + '/' + group)}"
            entry = devices.async_get_device(identifiers={(DOMAIN, device_id)})
            if entry is None:
                continue
            area = area_reg.async_get_area(entry.area_id) if entry.area_id else None
            found[group] = (entry, area.name if area else "")
        return found

    async def handle_learn_areas(call: ServiceCall) -> ServiceResponse:
        """Propose area rules from the areas already assigned by hand.

        Home Assistant holds the assignments and the add-on owns the
        rules, so they are collected here and sent over. Those assignments
        are the real knowledge about the building; deriving area names from
        path segments instead would leave a second set of near-duplicates
        beside them.
        """
        entries = hass.data.get(DOMAIN, {})
        if not entries:
            raise HomeAssistantError("Niagara BMS is not set up")
        coordinator = next(iter(entries.values()))

        # An area named after the Niagara station is the useless default
        # from when the area was a depth number, not somebody's decision.
        # Learning from it writes one rule per device cementing the very
        # bug this replaces — on this station that was 275 of 322.
        stations = {
            s for s in (_station_of_path(p.path)
                        for p in coordinator.points.values()) if s
        }
        found = _niagara_devices(coordinator)
        assignments = {
            g: area for g, (_e, area) in found.items()
            if area and area.lower() not in stations
        }
        ignored = sum(
            1 for _g, (_e, area) in found.items()
            if area and area.lower() in stations
        )
        if not assignments:
            return {
                "learned": 0,
                "ignored_station_default": ignored,
                "reason": (
                    "No Niagara device has an area somebody chose, so there "
                    "is nothing to learn from. Put a few devices in the "
                    "right areas first, then call this again."
                    + (f" {ignored} devices are in an area named after the "
                       "station, which is the default this replaces and not "
                       "a choice." if ignored else "")
                ),
            }

        session = async_get_clientsession(hass)
        try:
            async with session.post(
                f"{coordinator.addon_url}/api/areas/learn",
                json={"assignments": assignments,
                      "save": bool(call.data.get("save", False))},
                timeout=aiohttp.ClientTimeout(total=60),
            ) as response:
                response.raise_for_status()
                result = await response.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HomeAssistantError(f"Could not reach the add-on: {err}") from err

        result["learned_from"] = len(assignments)
        result["ignored_station_default"] = ignored
        return result

    async def handle_apply_areas(call: ServiceCall) -> ServiceResponse:
        """Move devices into the areas the rules give them.

        Only devices with no area, or sitting in an area named after the
        Niagara station — the known-bad default from when the area was a
        depth number. An area somebody chose deliberately is never
        overwritten: the rules were most likely learned from those very
        choices, and nothing here can tell a good one from a stale one.
        """
        entries = hass.data.get(DOMAIN, {})
        if not entries:
            raise HomeAssistantError("Niagara BMS is not set up")
        coordinator = next(iter(entries.values()))
        dry_run = bool(call.data.get("dry_run", True))

        stations = {
            s for s in (_station_of_path(p.path)
                        for p in coordinator.points.values()) if s
        }
        by_group = {}
        for point in coordinator.points.values():
            if point.group and point.group not in by_group:
                by_group[point.group] = point.area

        devices = dr.async_get(hass)
        area_reg = ar.async_get(hass)
        found = _niagara_devices(coordinator)

        moves, skipped = [], []
        for group, (entry, current) in sorted(found.items()):
            target = by_group.get(group)
            if not target or target == current:
                continue
            if current and current.lower() not in stations:
                skipped.append({"device": entry.name, "area": current,
                                "would_be": target})
                continue
            moves.append({"device": entry.name, "from": current or None,
                          "to": target})
            if not dry_run:
                area = area_reg.async_get_area_by_name(target)
                if area is None:
                    area = area_reg.async_create(target)
                devices.async_update_device(entry.id, area_id=area.id)

        return {
            "dry_run": dry_run,
            "moved": 0 if dry_run else len(moves),
            "would_move": len(moves) if dry_run else 0,
            "changes": moves[:40],
            "left_alone": len(skipped),
            "left_alone_detail": skipped[:20],
        }

    hass.services.async_register(
        DOMAIN, SERVICE_SYNC_HISTORY, handle_sync_history,
        schema=vol.Schema({vol.Optional("history"): str}),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_LEARN_AREAS, handle_learn_areas,
        schema=vol.Schema({vol.Optional("save", default=False): bool}),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_APPLY_AREAS, handle_apply_areas,
        schema=vol.Schema({vol.Optional("dry_run", default=True): bool}),
        supports_response=SupportsResponse.ONLY,
    )


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register integration services once."""
    if hass.services.has_service(DOMAIN, SERVICE_GENERATE_CARD):
        return
    await _async_register_extra_services(hass)

    async def handle_generate_card(call: ServiceCall) -> ServiceResponse:
        device = call.data[ATTR_DEVICE]

        entries = hass.data.get(DOMAIN, {})
        if not entries:
            raise HomeAssistantError("Niagara BMS is not set up")
        coordinator = next(iter(entries.values()))

        session = async_get_clientsession(hass)
        url = f"{coordinator.addon_url}/api/devices/card"
        try:
            async with session.get(
                url, params={"group": device},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                payload = await resp.json()
                if resp.status != 200:
                    raise HomeAssistantError(
                        payload.get("error", f"Add-on returned {resp.status}")
                    )
        except aiohttp.ClientError as err:
            raise HomeAssistantError(f"Cannot reach the add-on: {err}") from err

        card = payload.get("card")
        if not card:
            raise HomeAssistantError(f"No card defined for {device}")

        registry = er.async_get(hass)
        bindings: dict[str, str] = payload.get("bindings", {})
        mapping: dict[str, str] = {}
        missing: list[str] = []
        for slot_key, path in bindings.items():
            entity_id = _entity_id_for_path(registry, path)
            if entity_id:
                mapping[path] = entity_id
            else:
                missing.append(slot_key)

        resolved = _substitute(card, mapping)
        # A point may be bound but disabled, so it has no entity at all.
        resolved = _prune_unresolved(resolved, set(bindings.values()))

        if not resolved:
            raise HomeAssistantError(
                f"None of {device}'s bound points have entities in Home "
                "Assistant — enable them in the add-on first"
            )

        card_yaml = yaml.safe_dump(
            resolved, default_flow_style=False, sort_keys=False,
            allow_unicode=True, width=10000,
        )
        if missing:
            _LOGGER.debug(
                "Card for %s omits %d slot(s) with no entity: %s",
                device, len(missing), ", ".join(sorted(missing)),
            )

        return {
            "device": payload.get("device_name", device),
            "template": payload.get("template"),
            "card": resolved,
            "yaml": card_yaml,
            "unresolved_slots": sorted(missing),
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_GENERATE_CARD,
        handle_generate_card,
        schema=GENERATE_CARD_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    async def handle_generate_cards(call: ServiceCall) -> ServiceResponse:
        """Every published device's card, with the area it sits in.

        This exists for the dashboard strategy, which lays cards out by
        area and would otherwise need a call per switchboard.
        """
        entries = hass.data.get(DOMAIN, {})
        if not entries:
            raise HomeAssistantError("Niagara BMS is not set up")
        coordinator = next(iter(entries.values()))

        session = async_get_clientsession(hass)
        try:
            async with session.get(
                f"{coordinator.addon_url}/api/devices/cards",
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                payload = await resp.json()
                if resp.status != 200:
                    raise HomeAssistantError(
                        payload.get("error", f"Add-on returned {resp.status}")
                    )
        except aiohttp.ClientError as err:
            raise HomeAssistantError(f"Cannot reach the add-on: {err}") from err

        registry = er.async_get(hass)
        devices = dr.async_get(hass)
        wanted_area = (call.data.get("area") or "").strip().lower()

        out: list[dict[str, Any]] = []
        skipped: list[str] = []
        for entry in payload.get("devices", []):
            bindings = entry.get("bindings", {})
            mapping = {}
            for path in bindings.values():
                entity_id = _entity_id_for_path(registry, path)
                if entity_id:
                    mapping[path] = entity_id

            resolved = _substitute(entry["card"], mapping)
            resolved = _prune_unresolved(resolved, set(bindings.values()))
            if not resolved:
                skipped.append(entry["device_name"])
                continue

            # The area comes from the device registry, which is where a
            # person may have moved it by hand — not from the point path.
            area_id = ""
            for entity_id in mapping.values():
                ent = registry.async_get(entity_id)
                if ent is None:
                    continue
                if ent.area_id:
                    area_id = ent.area_id
                    break
                if ent.device_id:
                    device = devices.async_get(ent.device_id)
                    if device and device.area_id:
                        area_id = device.area_id
                        break

            if wanted_area and area_id.lower() != wanted_area:
                continue
            out.append({
                "device": entry["device_name"],
                "template": entry.get("template"),
                "faceplate": entry.get("faceplate", ""),
                "area_id": area_id,
                "card": resolved,
            })

        return {
            "count": len(out),
            "cards": out,
            # Named rather than dropped: a published device with no card
            # is usually a device whose points are all still disabled.
            "skipped": sorted(skipped),
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_GENERATE_CARDS,
        handle_generate_cards,
        schema=vol.Schema({vol.Optional("area"): str}),
        supports_response=SupportsResponse.ONLY,
    )


def async_unload_services(hass: HomeAssistant) -> None:
    for service in (
        SERVICE_GENERATE_CARD, SERVICE_FIX_STATISTICS, SERVICE_RUN_DIAGNOSTICS,
        SERVICE_RESCAN,
    ):
        hass.services.async_remove(DOMAIN, service)
