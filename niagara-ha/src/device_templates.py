"""Device type templates and point-to-slot binding.

A device is a folder of points. A template says what a given kind of
equipment should expose, as named slots with the unit and device class each
slot expects. Binding proposes which point fills which slot; the user
confirms or overrides in the sidebar.

This replaces guessing a point's meaning from its name at entity-creation
time. A slot declares intent up front, so an electricity meter's total
really is energy in kWh and not whatever a regex happened to match.

Templates are YAML. Built-ins ship in niagara-ha/templates/; users drop
their own in /config/niagara-ha/templates/ and they are merged on top, so a
site can add equipment types without a code change.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("niagara-ha.templates")

BUILTIN_TEMPLATE_DIR = Path("/app/templates")
USER_TEMPLATE_DIR = Path("/config/niagara-ha/templates")

_NIAGARA_ESCAPE_RE = re.compile(r"\$([0-9a-fA-F]{2})")


def decode_niagara_name(name: str) -> str:
    """Turn Niagara's $XX hex escapes into readable text.

    Match patterns are written against readable names, so "$33Phase_Active_Power"
    has to become "3Phase_Active_Power" before any glob is tried.
    """
    return _NIAGARA_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), name)


@dataclass
class Slot:
    """One named role a template expects a point to fill."""

    key: str
    name: str
    required: bool = False
    device_class: str | None = None
    state_class: str | None = None
    units: list[str] = field(default_factory=list)
    point_types: list[str] = field(default_factory=list)
    match: list[str] = field(default_factory=list)
    validation: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "Slot":
        return cls(
            key=data["key"],
            name=data.get("name", data["key"]),
            required=bool(data.get("required", False)),
            device_class=data.get("device_class"),
            state_class=data.get("state_class"),
            units=list(data.get("units", [])),
            point_types=list(data.get("point_types", [])),
            match=list(data.get("match", [])),
            validation=dict(data.get("validation", {})),
        )


@dataclass
class Template:
    """A kind of equipment: electricity meter, AHU, pump, and so on."""

    id: str
    name: str
    description: str = ""
    icon: str | None = None
    slots: list[Slot] = field(default_factory=list)
    # Globs matched against the device folder's own name. A folder called
    # "SOLAR" is a strong hint no amount of point-name matching gives, since
    # a solar meter's points look like any other meter's.
    device_match: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "Template":
        return cls(
            id=data["id"],
            name=data.get("name", data["id"]),
            description=data.get("description", ""),
            icon=data.get("icon"),
            slots=[Slot.from_dict(s) for s in data.get("slots", [])],
            device_match=list(data.get("device_match", [])),
        )

    def matches_device_name(self, group: str) -> bool:
        if not self.device_match:
            return False
        leaf = decode_niagara_name(group.rstrip("/").split("/")[-1]).lower()
        return any(fnmatch(leaf, p.lower()) for p in self.device_match)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "slots": [
                {
                    "key": s.key, "name": s.name, "required": s.required,
                    "device_class": s.device_class, "state_class": s.state_class,
                    "units": s.units, "match": s.match,
                }
                for s in self.slots
            ],
        }

    @property
    def required_slots(self) -> list[Slot]:
        return [s for s in self.slots if s.required]


def _load_dir(path: Path) -> dict[str, Template]:
    templates: dict[str, Template] = {}
    if not path.is_dir():
        return templates
    for file in sorted(path.glob("*.yaml")):
        try:
            with open(file) as handle:
                data = yaml.safe_load(handle)
        except (OSError, yaml.YAMLError) as err:
            logger.warning("Skipping template %s: %s", file.name, err)
            continue
        if not isinstance(data, dict) or "id" not in data:
            logger.warning("Skipping template %s: missing an id", file.name)
            continue
        try:
            template = Template.from_dict(data)
        except (KeyError, TypeError) as err:
            logger.warning("Skipping template %s: %s", file.name, err)
            continue
        templates[template.id] = template
    return templates


def load_templates() -> dict[str, Template]:
    """Built-in templates, with any user templates merged over the top."""
    templates = _load_dir(BUILTIN_TEMPLATE_DIR)
    user = _load_dir(USER_TEMPLATE_DIR)
    if user:
        logger.info("Loaded %d user templates from %s", len(user), USER_TEMPLATE_DIR)
    templates.update(user)
    return templates


# -- Binding -------------------------------------------------------------

# A pattern matching earlier in a slot's list is a better fit than a later
# one; a unit the slot actually declares outweighs any name match.
_UNIT_BONUS = 1000
_EXACT_NAME_BONUS = 500


def score_candidate(slot: Slot, point: dict) -> int | None:
    """How well a point fits a slot, or None if it cannot fill it at all."""
    name = decode_niagara_name(point.get("name", "")).lower()
    if not name:
        return None

    if slot.point_types and point.get("type") not in slot.point_types:
        return None

    score: int | None = None
    for index, pattern in enumerate(slot.match):
        pattern = pattern.lower()
        if fnmatch(name, pattern):
            score = len(slot.match) - index
            if pattern.strip("*") == name:
                score += _EXACT_NAME_BONUS
            break
    if score is None:
        return None

    unit = (point.get("unit") or "").strip()
    if slot.units:
        if unit and unit in slot.units:
            score += _UNIT_BONUS
        elif unit:
            # The point reports a unit this slot does not accept. Binding it
            # would produce exactly the contradicted device classes templates
            # exist to prevent.
            return None
    return score


def bind_template(template: Template, points: list[dict]) -> dict[str, str | None]:
    """Propose a point for each slot. Returns {slot_key: path or None}.

    Required slots are filled first so an optional slot cannot steal a point
    a required one needs. No point fills two slots.
    """
    candidates: dict[str, list[tuple[int, str]]] = {}
    for slot in template.slots:
        scored = []
        for point in points:
            score = score_candidate(slot, point)
            if score is not None:
                scored.append((score, point.get("path", "")))
        scored.sort(key=lambda item: (-item[0], item[1]))
        candidates[slot.key] = scored

    bindings: dict[str, str | None] = {}
    taken: set[str] = set()
    for slot in sorted(template.slots, key=lambda s: not s.required):
        chosen = None
        for _score, path in candidates[slot.key]:
            if path and path not in taken:
                chosen = path
                taken.add(path)
                break
        bindings[slot.key] = chosen
    return bindings


# A folder named for its equipment outweighs a slot or two of point matching.
_DEVICE_NAME_BONUS = 100


def suggest_template(
    templates: dict[str, Template], points: list[dict], group: str = "",
) -> tuple[str | None, int]:
    """Guess which template best fits a device.

    A template only qualifies if every required slot can be filled. Among
    those, the one matching the folder's name wins; otherwise the one filling
    the most optional slots.
    """
    best_id, best_score = None, 0
    for template in templates.values():
        required = template.required_slots
        if not required:
            continue
        bindings = bind_template(template, points)
        if any(not bindings.get(s.key) for s in required):
            continue

        optional = sum(
            1 for s in template.slots if not s.required and bindings.get(s.key)
        )
        score = len(required) * 10 + optional
        if group and template.matches_device_name(group):
            score += _DEVICE_NAME_BONUS
        if score > best_score:
            best_id, best_score = template.id, score
    return best_id, best_score


# -- Persistence ---------------------------------------------------------

DEVICE_TYPES_FILE = Path("/config/niagara-ha/device_types.yaml")

# Where a device sits on the way to Home Assistant. Only published devices
# are exposed, so typing and checking a device is a deliberate act rather
# than something that happens the moment a point is enabled.
STATE_DRAFT = "draft"
STATE_PUBLISHED = "published"
VALID_STATES = (STATE_DRAFT, STATE_PUBLISHED)


def load_device_types() -> dict[str, dict]:
    """Return {group: {"template", "bindings", "state"}}."""
    try:
        with open(DEVICE_TYPES_FILE) as handle:
            data = yaml.safe_load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, yaml.YAMLError) as err:
        logger.warning("Could not read %s: %s", DEVICE_TYPES_FILE, err)
        return {}

    if not isinstance(data, dict):
        return {}
    devices = data.get("devices") or {}
    if not isinstance(devices, dict):
        return {}

    out: dict[str, dict] = {}
    for group, entry in devices.items():
        if not isinstance(entry, dict) or not entry.get("template"):
            continue
        bindings = entry.get("bindings") or {}
        out[group] = {
            "template": entry["template"],
            "bindings": {k: v for k, v in bindings.items() if v},
            "state": entry.get("state") if entry.get("state") in VALID_STATES
            else STATE_DRAFT,
        }
    return out


def save_device_types(devices: dict[str, dict]) -> None:
    """Write device types atomically, so a crash cannot truncate the file."""
    DEVICE_TYPES_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_comment": (
            "Device types assign a template to a folder of points. Only "
            "devices with state: published are exposed to Home Assistant."
        ),
        "devices": {
            group: {
                "template": entry["template"],
                "bindings": entry.get("bindings", {}),
                "state": entry.get("state", STATE_DRAFT),
            }
            for group, entry in sorted(devices.items())
        },
    }
    tmp = DEVICE_TYPES_FILE.with_suffix(".tmp")
    with open(tmp, "w") as handle:
        yaml.safe_dump(payload, handle, default_flow_style=False, sort_keys=False,
                       allow_unicode=True, width=10000)
    tmp.replace(DEVICE_TYPES_FILE)
