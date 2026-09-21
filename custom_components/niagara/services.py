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
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from .coordinator import stable_id

_LOGGER = logging.getLogger(__name__)

SERVICE_GENERATE_CARD = "generate_card"
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
            if key in ("entity", "entities") and resolved is None:
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
            if key in ("entity", "entities") and pruned is None:
                return None
            if pruned is None:
                continue
            out[key] = pruned
        return out
    return node


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register integration services once."""
    if hass.services.has_service(DOMAIN, SERVICE_GENERATE_CARD):
        return

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


def async_unload_services(hass: HomeAssistant) -> None:
    hass.services.async_remove(DOMAIN, SERVICE_GENERATE_CARD)
