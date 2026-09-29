"""The faceplate catalogue: which graphic a generated card can wear.

A template matches equipment of every make — an electricity meter template
binds a Circutor, a Schneider and an unbranded DIN analyser equally well —
so the faceplate is a property of the device, not of the type. This module
holds the list of faces available and swaps one into a rendered card.

`faceplates.json` is a copy of `faceplates/index.json` from the cards
repository, vendored so the add-on works with no network and so a card
bundle and the add-on that configures it cannot disagree at runtime.
Refresh it with `scripts/sync-faceplates.sh` when the cards release.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CATALOGUE_FILE = Path(__file__).resolve().parent.parent / "faceplates.json"


def load_faceplates() -> list[dict]:
    """Every faceplate the bundled cards can render."""
    try:
        with open(CATALOGUE_FILE) as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as err:
        logger.warning("Could not read %s: %s", CATALOGUE_FILE, err)
        return []
    entries = data.get("faceplates") if isinstance(data, dict) else data
    return entries if isinstance(entries, list) else []


def faceplate_card(faceplate_id: str) -> str | None:
    """The custom card that renders a faceplate, or None if unknown."""
    for entry in load_faceplates():
        if entry.get("id") == faceplate_id:
            return entry.get("card")
    return None


def faceplates_for_card(card_type: str) -> list[dict]:
    """The faces a given custom card can wear, for the picker in the UI."""
    wanted = card_type.removeprefix("custom:")
    return [f for f in load_faceplates() if f.get("card") == wanted]


def roles_in(card: Any, card_type: str) -> set[str]:
    """The roles a rendered card binds for one custom card type."""
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == card_type:
                entities = node.get("entities")
                if isinstance(entities, dict):
                    found.update(entities)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(card)
    return found


def card_types_in(card: Any) -> list[str]:
    """Every custom card type used anywhere in a rendered card tree."""
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            kind = node.get("type")
            if isinstance(kind, str) and kind.startswith("custom:"):
                if kind not in found:
                    found.append(kind)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(card)
    return found


def apply_faceplate(card: Any, faceplate_id: str) -> Any:
    """Swap the faceplate on whichever custom card can wear it.

    A card tree may hold more than one custom card, so the swap is targeted
    by the faceplate's own card type rather than applied to the first one
    found. An unknown faceplate leaves the card untouched: the template's
    default is always a working card, and silently rendering nothing would
    be worse than ignoring a bad setting.
    """
    target = faceplate_card(faceplate_id)
    if not target:
        logger.warning("Unknown faceplate %s; keeping the default", faceplate_id)
        return card

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out = {k: walk(v) for k, v in node.items()}
            if out.get("type") == f"custom:{target}":
                out["faceplate"] = faceplate_id
            return out
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    return walk(card)
