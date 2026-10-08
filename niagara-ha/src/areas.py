"""Mapping device folders onto Home Assistant areas.

The area was derived from a single depth number: take the Nth segment of a
point's path. That cannot work on a real station, because the segment that
names a place is at a different depth in every branch. On the reference
station it put **275 of 323 devices into one area named after the station**,
which is the whole building in one bucket.

The useful level genuinely varies:

    .../MercureBMS/HVAC/AC/Rooms/331      the device *is* the room
    .../MercureBMS/Kitchens/Main_Kitchen/Fridge-1   the parent is the place
    .../MercureBMS/Electrical/DB-L5.1-Power         the parent is the place
    .../MercureBMS/MISC/HYDRAULICS                  the device is the place

So it is rules, in order, first match wins — and rules that name the areas
somebody has already created by hand rather than inventing new ones from
path segments. A station where nobody writes any rules keeps the old
behaviour exactly.
"""

import logging
import re
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterable, Optional

import yaml

logger = logging.getLogger("niagara-ha.areas")

AREAS_FILE = Path("/config/niagara-ha/areas.yaml")

FILE_COMMENT = (
    "Rules mapping device folders to Home Assistant areas, in order — the "
    "first match wins. 'match' is a glob against the device's folder path. "
    "'area' is the area name; it should be one that already exists in Home "
    "Assistant. Use {name} in an area to mean the device folder's own name "
    "and {parent} for the folder above it."
)


@dataclass
class AreaRule:
    match: str
    area: str
    note: str = ""

    def to_dict(self) -> dict:
        out = {"match": self.match, "area": self.area}
        if self.note:
            out["note"] = self.note
        return out


def decode(name: str) -> str:
    """Niagara's $xx escapes, which appear in folder names."""
    return re.sub(r"\$([0-9a-fA-F]{2})",
                  lambda m: chr(int(m.group(1), 16)), name)


def load() -> list[AreaRule]:
    try:
        raw = yaml.safe_load(AREAS_FILE.read_text()) or {}
    except FileNotFoundError:
        return []
    except (yaml.YAMLError, OSError) as e:
        # A damaged file must not take area mapping down to nothing; the
        # fallback behaviour is what every station had before rules existed.
        logger.error("Could not read %s: %s", AREAS_FILE, e)
        return []
    rules = raw.get("rules") if isinstance(raw, dict) else None
    if not isinstance(rules, list):
        return []
    out = []
    for item in rules:
        if not isinstance(item, dict):
            continue
        match = str(item.get("match") or "").strip()
        area = str(item.get("area") or "").strip()
        if match and area:
            out.append(AreaRule(match, area, str(item.get("note") or "")))
    return out


def save(rules: Iterable[AreaRule]) -> None:
    AREAS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"_comment": FILE_COMMENT,
               "rules": [r.to_dict() for r in rules]}
    tmp = AREAS_FILE.with_suffix(".tmp")
    with open(tmp, "w") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False, default_flow_style=False)
    tmp.replace(AREAS_FILE)


def _folder_parts(group: str) -> list[str]:
    return [decode(p) for p in group.split("/") if p]


def resolve(group: str, rules: Iterable[AreaRule]) -> Optional[str]:
    """The area a device folder maps to, or None if no rule claims it.

    Matching is against the decoded folder path, so a rule can be written
    with the names somebody reading Workbench would recognise rather than
    with `$2d` in it.
    """
    parts = _folder_parts(group)
    if not parts:
        return None
    path = "/".join(parts)
    name = parts[-1]
    parent = parts[-2] if len(parts) > 1 else ""

    for rule in rules:
        pattern = rule.match
        # A rule without a wildcard is a prefix, which is what somebody
        # means by "Kitchens" — not "the folder called exactly Kitchens".
        if not any(ch in pattern for ch in "*?["):
            matched = path == pattern or path.startswith(pattern.rstrip("/") + "/")
        else:
            matched = fnmatch(path, pattern)
        if not matched:
            continue
        area = rule.area.replace("{name}", name).replace("{parent}", parent)
        return area.strip() or None
    return None


def _common_prefix(members: list[list[str]]) -> list[str]:
    shared: list[str] = []
    for index in range(min(len(m) for m in members)):
        segment = members[0][index]
        if all(m[index] == segment for m in members):
            shared.append(segment)
        else:
            break
    return shared


# How much of a folder an area must account for before a prefix rule is
# taken. Exclusivity against the *assigned* devices is not enough: two
# rooms assigned inside a folder of 192 made that folder look exclusive,
# and the rule would have moved all 192 into one of them.
PREFIX_COVERAGE = 0.5


def learn(
    assignments: dict[str, str],
    min_devices: int = 2,
    all_groups: Iterable[str] = (),
) -> list[AreaRule]:
    """Propose rules from the areas somebody has already assigned by hand.

    `assignments` maps a device folder to the area it is currently in.
    Those assignments are the real knowledge about the building, and
    inventing area names out of path segments would throw them away and
    leave a second set of near-duplicates beside them.

    A shared prefix is only a rule if **no other area's devices sit under
    it**. That caveat is the whole difficulty: on the reference station,
    five areas — a loading bay, three comms rooms and the lifts — all have
    their devices under `Electrical`, and one area is as good a claim to
    that folder as another. Taking the prefix anyway gave the first of them
    all twenty-nine boards, and "Kitchen - Main" a prefix covering the
    entire station.

    So an area whose prefix is not exclusive gets one exact rule per
    device. Verbose, but it reproduces every assignment exactly, and a
    rule that is right about 47 devices beats one that is wrong about 275.
    """
    by_area: dict[str, list[list[str]]] = {}
    for group, area in assignments.items():
        if not area:
            continue
        parts = _folder_parts(group)
        if parts:
            by_area.setdefault(area, []).append(parts)

    # Every device folder there is, not only the assigned ones, so a
    # prefix can be judged against what actually sits under it.
    everything = [_folder_parts(g) for g in all_groups] or [
        parts for members in by_area.values() for parts in members
    ]

    def owners_under(prefix: list[str]) -> set[str]:
        """Every area with a device under this prefix."""
        found = set()
        for area, members in by_area.items():
            for parts in members:
                if parts[:len(prefix)] == prefix:
                    found.add(area)
                    break
        return found

    def coverage(prefix: list[str], members: list[list[str]]) -> float:
        """What share of the folders under this prefix belong to the area."""
        under = sum(1 for parts in everything
                    if parts[:len(prefix)] == prefix)
        if not under:
            return 0.0
        return len(members) / under

    rules: list[AreaRule] = []
    for area, members in sorted(by_area.items()):
        prefix = _common_prefix(members) if len(members) >= min_devices else []
        share = coverage(prefix, members) if prefix else 0.0
        if (prefix and owners_under(prefix) == {area}
                and share >= PREFIX_COVERAGE):
            rules.append(AreaRule(
                match="/".join(prefix), area=area,
                note=(f"learned from {len(members)} devices already in this "
                      f"area, {share:.0%} of that folder"),
            ))
            continue

        # No exclusive prefix: name each device. The note says why, so
        # somebody who knows the building can replace the lot with one
        # structural rule.
        reason = (
            (f"covers only {share:.0%} of that folder, so each device is "
             "named") if prefix and owners_under(prefix) == {area} else
            "shares a folder with other areas, so each device is named"
            if prefix else
            ("only one device in this area" if len(members) < min_devices
             else "these devices share no folder of their own")
        )
        for parts in sorted(members):
            rules.append(AreaRule(
                match="/".join(parts), area=area, note=reason,
            ))

    # Longest match first, so a specific rule is never shadowed by a
    # general one it sits inside.
    rules.sort(key=lambda r: (-len(r.match), r.area))
    return rules


def preview(
    groups: Iterable[str], rules: Iterable[AreaRule],
) -> dict[str, Optional[str]]:
    """What each device folder would map to."""
    rules = list(rules)
    return {group: resolve(group, rules) for group in groups}
