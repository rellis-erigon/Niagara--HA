"""Checking templates against the faceplate catalogue they draw with.

The faceplates live in a different repository from the templates that use
them, and nothing tied the two together. A faceplate that renames a role
leaves every template still sending the old key: the region binds nothing,
draws its placeholder, and looks like a device with a missing point. A
template naming a faceplate that is not in the bundled catalogue is worse
again — `apply_faceplate` logs a warning nobody reads and silently keeps
the template's default, which is how the refrigeration faces appeared to
be applied for a while without being.

Neither failure throws, so both need looking for on purpose. This is run
by the add-on's tests, so a mismatch fails the build, and by the
diagnostics, so one that reaches a running station is visible.
"""

import logging
from typing import Any, Iterable

logger = logging.getLogger("niagara-ha.faceplate_contract")

# A role a card may bind that no faceplate declares, because the card
# itself consumes it rather than a region.
CARD_LEVEL_ROLES = frozenset({"name", "title"})


def card_faceplates(card: Any) -> list[dict]:
    """Every faceplate a rendered card names, with what it sends to it.

    Returns one entry per custom card node carrying a `faceplate`, holding
    the faceplate id, the roles bound to it and the option keys set on it.
    A card tree may hold several, which is why this is a list.
    """
    found: list[dict] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            kind = node.get("type")
            face = node.get("faceplate")
            if isinstance(kind, str) and kind.startswith("custom:") and face:
                entities = node.get("entities")
                options = node.get("options")
                found.append({
                    "faceplate": str(face),
                    "card": kind.removeprefix("custom:"),
                    "roles": set(entities) if isinstance(entities, dict) else set(),
                    "options": set(options) if isinstance(options, dict) else set(),
                })
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(card)
    return found


def _index(catalogue: Iterable[dict]) -> dict[str, dict]:
    return {str(f.get("id")): f for f in catalogue if f.get("id")}


def check_card(
    card: Any, catalogue: Iterable[dict], label: str = "",
) -> list[dict]:
    """Problems in one rendered card. Empty means it agrees with the faces."""
    faces = _index(catalogue)
    problems: list[dict] = []

    for use in card_faceplates(card):
        face = faces.get(use["faceplate"])
        if face is None:
            problems.append({
                "kind": "unknown_faceplate",
                "where": label,
                "faceplate": use["faceplate"],
                "message": (
                    f"{label or 'card'} draws with faceplate "
                    f"{use['faceplate']!r}, which is not in the catalogue. "
                    "The card silently keeps its default instead. If the "
                    "faceplate is new, faceplates.json needs refreshing "
                    "from the cards repository."
                ),
            })
            continue

        if face.get("card") and face["card"] != use["card"]:
            problems.append({
                "kind": "wrong_card",
                "where": label,
                "faceplate": use["faceplate"],
                "message": (
                    f"{label or 'card'} puts faceplate {use['faceplate']!r} "
                    f"on custom:{use['card']}, but it is drawn by "
                    f"custom:{face['card']}."
                ),
            })

        declared = set(face.get("roles") or [])
        sent = use["roles"] - CARD_LEVEL_ROLES
        unknown = sorted(sent - declared)
        if unknown and declared:
            problems.append({
                "kind": "unknown_roles",
                "where": label,
                "faceplate": use["faceplate"],
                "roles": unknown,
                "message": (
                    f"{label or 'card'} binds {', '.join(unknown)} to "
                    f"faceplate {use['faceplate']!r}, which declares no such "
                    "role. Those bindings draw nothing — most likely the "
                    "role was renamed in the cards repository."
                ),
            })

        option_keys = {
            str(o.get("key")) for o in (face.get("options") or [])
            if o.get("key")
        }
        stray = sorted(use["options"] - option_keys)
        if stray:
            problems.append({
                "kind": "unknown_options",
                "where": label,
                "faceplate": use["faceplate"],
                "options": stray,
                "message": (
                    f"{label or 'card'} sets option(s) {', '.join(stray)} on "
                    f"faceplate {use['faceplate']!r}, which does not take "
                    "them. They are ignored."
                ),
            })

    return problems


def check_templates(templates: dict, catalogue: Iterable[dict]) -> list[dict]:
    """Every template's card block against the catalogue."""
    catalogue = list(catalogue)
    problems: list[dict] = []
    for template_id, template in sorted(templates.items()):
        card = getattr(template, "card", None)
        if not card:
            continue
        problems.extend(check_card(card, catalogue, f"template {template_id}"))
    return problems


def check_devices(
    devices: dict, templates: dict, catalogue: Iterable[dict],
) -> list[dict]:
    """Faceplates chosen per device, which templates cannot vouch for.

    A device may wear any face its card can render, picked by hand in the
    UI. The catalogue it was picked from is the one that was bundled at the
    time, so a face can go missing under a device the same way it can under
    a template.
    """
    catalogue = list(catalogue)
    faces = _index(catalogue)
    problems: list[dict] = []
    for group, entry in sorted(devices.items()):
        chosen = (entry.get("faceplate") or "").strip()
        if not chosen:
            continue
        if chosen not in faces:
            problems.append({
                "kind": "unknown_faceplate",
                "where": f"device {group}",
                "faceplate": chosen,
                "message": (
                    f"Device {group} is set to faceplate {chosen!r}, which "
                    "is not in the catalogue, so it is drawn with its "
                    "template's default instead."
                ),
            })
            continue
        option_keys = {
            str(o.get("key")) for o in (faces[chosen].get("options") or [])
            if o.get("key")
        }
        stray = sorted(set(entry.get("faceplate_options") or {}) - option_keys)
        if stray:
            problems.append({
                "kind": "unknown_options",
                "where": f"device {group}",
                "faceplate": chosen,
                "options": stray,
                "message": (
                    f"Device {group} sets option(s) {', '.join(stray)} on "
                    f"faceplate {chosen!r}, which does not take them."
                ),
            })
    return problems
