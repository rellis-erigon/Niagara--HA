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
    # A Lovelace card definition whose entity fields are "{{slot.<key>}}"
    # placeholders. A template already knows what each slot means, which is
    # exactly what a card needs — so the card is declared once here rather
    # than rebuilt by hand for every device of that type.
    card: dict[str, Any] = field(default_factory=dict)
    # Built-ins ship in the image and are read-only; a user template with the
    # same id shadows one, which is how you customise without forking.
    builtin: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "Template":
        return cls(
            id=data["id"],
            name=data.get("name", data["id"]),
            description=data.get("description", ""),
            icon=data.get("icon"),
            slots=[Slot.from_dict(s) for s in data.get("slots", [])],
            device_match=list(data.get("device_match", [])),
            card=dict(data.get("card") or {}),
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
            "builtin": self.builtin,
            "device_match": self.device_match,
            "card": self.card,
            "slots": [
                {
                    "key": s.key, "name": s.name, "required": s.required,
                    "device_class": s.device_class, "state_class": s.state_class,
                    "units": s.units, "point_types": s.point_types,
                    "match": s.match, "validation": s.validation,
                }
                for s in self.slots
            ],
        }

    @property
    def required_slots(self) -> list[Slot]:
        return [s for s in self.slots if s.required]


def _load_dir(path: Path, builtin: bool = False) -> dict[str, Template]:
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
        template.builtin = builtin
        templates[template.id] = template
    return templates


def load_templates() -> dict[str, Template]:
    """Built-in templates, with any user templates merged over the top."""
    templates = _load_dir(BUILTIN_TEMPLATE_DIR, builtin=True)
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


# Meters expose period accumulators beside their lifetime total —
# DailyUsage, TodaysTotal, ThisWeek. They reset, so they can never fill a
# slot declaring monotonic: a main incomer bound to DailyUsage reads 1,726
# kWh instead of 5,540,147 and quietly breaks the energy dashboard.
#
# This only constrains automatic binding. A user can still bind one by hand,
# because a station may genuinely expose nothing else.
_RESETTING_RE = re.compile(
    r"daily|weekly|monthly|yearly|hourly|today|yesterday"
    r"|this(week|month|year|day)|last(week|month|year)"
    r"|current(week|month|year)",
    re.I,
)


def is_resetting_register(name: str) -> bool:
    return bool(_RESETTING_RE.search(decode_niagara_name(name or "")))


def effective_unit(point: dict) -> str:
    """A point's unit, preferring a user override of what Niagara reported."""
    return (point.get("custom_unit") or point.get("unit") or "").strip()


def score_candidate(slot: Slot, point: dict) -> int | None:
    """How well a point fits a slot, or None if it cannot fill it at all."""
    name = decode_niagara_name(point.get("name", "")).lower()
    if not name:
        return None

    if slot.point_types and point.get("type") not in slot.point_types:
        return None

    if slot.validation.get("monotonic") and is_resetting_register(
        point.get("name", "")
    ):
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

    unit = effective_unit(point)
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


# -- User template editing -----------------------------------------------

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class TemplateError(ValueError):
    """A template payload that cannot be stored as written."""


def validate_template_payload(data: Any) -> dict:
    """Check a template submitted from the editor before writing it to disk.

    A malformed template would be skipped silently at load time, leaving the
    user staring at a template that saved but never appears.
    """
    if not isinstance(data, dict):
        raise TemplateError("Template must be an object")

    template_id = str(data.get("id", "")).strip().lower()
    if not _ID_RE.match(template_id):
        raise TemplateError(
            "Id must be lowercase letters, digits, dash or underscore",
        )

    name = str(data.get("name", "")).strip() or template_id
    slots_in = data.get("slots")
    if not isinstance(slots_in, list):
        raise TemplateError("Template must have a list of slots")

    seen: set[str] = set()
    slots = []
    for raw in slots_in:
        if not isinstance(raw, dict):
            raise TemplateError("Each slot must be an object")
        key = str(raw.get("key", "")).strip().lower()
        if not _ID_RE.match(key):
            raise TemplateError(f"Slot key {key!r} is not a valid identifier")
        if key in seen:
            raise TemplateError(f"Duplicate slot key {key!r}")
        seen.add(key)

        slot: dict[str, Any] = {
            "key": key,
            "name": str(raw.get("name", "")).strip() or key,
            "required": bool(raw.get("required", False)),
        }
        for field_name in ("device_class", "state_class"):
            value = raw.get(field_name)
            if value:
                slot[field_name] = str(value).strip()
        for field_name in ("units", "point_types", "match"):
            values = raw.get(field_name) or []
            if not isinstance(values, list):
                raise TemplateError(f"{field_name} must be a list")
            cleaned = [str(v).strip() for v in values if str(v).strip()]
            if cleaned:
                slot[field_name] = cleaned

        rules = raw.get("validation") or {}
        if rules:
            if not isinstance(rules, dict):
                raise TemplateError("validation must be an object")
            out: dict[str, Any] = {}
            for bound in ("min", "max"):
                if rules.get(bound) not in (None, ""):
                    try:
                        out[bound] = float(rules[bound])
                    except (TypeError, ValueError):
                        raise TemplateError(f"validation.{bound} must be a number")
            if rules.get("monotonic"):
                out["monotonic"] = True
            if out:
                slot["validation"] = out

        slots.append(slot)

    payload = {
        "id": template_id,
        "name": name,
        "description": str(data.get("description", "")).strip(),
        "slots": slots,
    }
    if data.get("icon"):
        payload["icon"] = str(data["icon"]).strip()
    card = data.get("card")
    if card:
        if not isinstance(card, dict):
            raise TemplateError("card must be an object")
        if not card.get("type"):
            raise TemplateError("card needs a type, e.g. entities")
        payload["card"] = card

    device_match = data.get("device_match") or []
    if isinstance(device_match, list):
        cleaned = [str(v).strip() for v in device_match if str(v).strip()]
        if cleaned:
            payload["device_match"] = cleaned
    return payload


def save_user_template(data: Any) -> dict:
    """Write a user template. A built-in id is shadowed, never overwritten."""
    payload = validate_template_payload(data)
    USER_TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    path = USER_TEMPLATE_DIR / f"{payload['id']}.yaml"
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as handle:
        yaml.safe_dump(payload, handle, default_flow_style=False,
                       sort_keys=False, allow_unicode=True, width=10000)
    tmp.replace(path)
    logger.info("Saved user template %s", payload["id"])
    return payload


def delete_user_template(template_id: str) -> bool:
    """Remove a user template. Built-ins are untouched, so a shadowed id
    reverts to the shipped version rather than disappearing."""
    if not _ID_RE.match(str(template_id or "")):
        return False
    path = USER_TEMPLATE_DIR / f"{template_id}.yaml"
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    except OSError as err:
        logger.warning("Could not delete template %s: %s", template_id, err)
        return False
    logger.info("Deleted user template %s", template_id)
    return True


# -- Card rendering ------------------------------------------------------

# Accepts "{{slot.room_temperature}}" and bare "{{device_name}}".
_SLOT_TOKEN_RE = re.compile(
    r"^\s*\{\{\s*(?:slot\.)?([a-z0-9_-]+)\s*\}\}\s*$", re.I,
)


def resolve_card(card: Any, mapping: dict[str, str | None]) -> Any:
    """Replace {{slot.key}} placeholders with whatever mapping provides.

    Anything referring to a slot with no replacement is dropped rather than
    left dangling — a card naming an entity that does not exist renders as a
    broken row, which is worse than a shorter card.
    """
    if isinstance(card, str):
        match = _SLOT_TOKEN_RE.match(card)
        if match:
            return mapping.get(match.group(1).lower())
        return card

    if isinstance(card, list):
        out = []
        for item in card:
            resolved = resolve_card(item, mapping)
            if resolved is None:
                continue
            if isinstance(resolved, dict) and resolved.get("__drop__"):
                continue
            out.append(resolved)
        return out

    if isinstance(card, dict):
        out: dict[str, Any] = {}
        for key, item in card.items():
            resolved = resolve_card(item, mapping)
            if resolved is None:
                # A row whose entity is unresolved is not a row at all.
                if key in ("entity", "entities"):
                    return {"__drop__": True}
                continue
            out[key] = resolved
        return out

    return card


def render_card(template: "Template", mapping: dict[str, str | None]) -> dict | None:
    if not template.card:
        return None
    rendered = resolve_card(template.card, mapping)
    if not isinstance(rendered, dict) or rendered.get("__drop__"):
        return None
    return rendered


# -- Import / export -----------------------------------------------------

def export_templates(templates: list["Template"]) -> str:
    """Serialise templates as a multi-document YAML file."""
    docs = []
    for template in templates:
        doc: dict[str, Any] = {
            "id": template.id,
            "name": template.name,
            "description": template.description,
        }
        if template.icon:
            doc["icon"] = template.icon
        if template.device_match:
            doc["device_match"] = template.device_match
        doc["slots"] = [
            {k: val for k, val in (
                ("key", s.key), ("name", s.name), ("required", s.required),
                ("device_class", s.device_class), ("state_class", s.state_class),
                ("units", s.units), ("point_types", s.point_types),
                ("match", s.match), ("validation", s.validation),
            ) if val not in (None, [], {}, False) or k in ("key", "name")}
            for s in template.slots
        ]
        if template.card:
            doc["card"] = template.card
        docs.append(doc)
    return yaml.safe_dump_all(
        docs, default_flow_style=False, sort_keys=False,
        allow_unicode=True, width=10000,
    )


def import_templates(text: str) -> tuple[list[str], list[str]]:
    """Load one or many templates from YAML. Returns (saved ids, errors).

    Each document is validated and saved independently, so one bad template
    in a shared file does not discard the rest.
    """
    try:
        docs = list(yaml.safe_load_all(text))
    except yaml.YAMLError as err:
        raise TemplateError(f"Could not parse YAML: {err}") from err

    saved: list[str] = []
    errors: list[str] = []
    for index, doc in enumerate(docs, 1):
        if doc is None:
            continue
        if isinstance(doc, list):
            docs.extend(doc)
            continue
        try:
            payload = save_user_template(doc)
            saved.append(payload["id"])
        except TemplateError as err:
            name = doc.get("id", f"document {index}") if isinstance(doc, dict) else f"document {index}"
            errors.append(f"{name}: {err}")
    if not saved and not errors:
        raise TemplateError("No templates found in that YAML")
    return saved, errors
