"""Surface problems as Home Assistant repair issues.

Two sources. The add-on's Diagnostics view finds things about the station;
this integration finds one thing about Home Assistant itself — statistics
recorded under a unit the entity no longer reports.

Deliberately not every finding becomes an issue. Only the ones where Home
Assistant is recording or showing something untrue, rather than merely
missing something. An issue raised for a condition the user already knows
about and cannot act on from here teaches people to ignore the panel.
"""
from __future__ import annotations

import logging

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Add-on finding id -> severity. These are informational: the fix is in
# Niagara or in the add-on's own UI, not something Home Assistant can do.
SURFACED = {
    "version_drift": ir.IssueSeverity.ERROR,
    "decreasing_totals": ir.IssueSeverity.WARNING,
    "resetting_totals": ir.IssueSeverity.WARNING,
    "energy_ineligible": ir.IssueSeverity.WARNING,
}

STATISTICS_ISSUE = "statistics_unit_conflict"
LEARN_MORE = "https://github.com/rellis-erigon/Niagara--HA#diagnostics"


def _issue_id(finding_id: str) -> str:
    return f"diag_{finding_id}"


def async_sync_issues(hass: HomeAssistant, report: dict) -> None:
    """Raise, update or clear issues to match the latest diagnostics report.

    Called on every refresh, so an issue clears itself once the underlying
    problem is fixed. A repair you must dismiss by hand after fixing it is a
    repair people stop trusting.
    """
    findings = {f["id"]: f for f in report.get("findings", [])}

    for finding_id, severity in SURFACED.items():
        issue_id = _issue_id(finding_id)
        finding = findings.get(finding_id)

        if finding is None:
            ir.async_delete_issue(hass, DOMAIN, issue_id)
            continue

        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=severity,
            translation_key=finding_id,
            translation_placeholders={
                "title": finding.get("title", ""),
                "detail": finding.get("detail", ""),
                "action": finding.get("action", ""),
                "count": str(finding.get("count", 0)),
            },
            learn_more_url=LEARN_MORE,
        )


def async_set_statistics_issue(hass: HomeAssistant, conflicts: int) -> None:
    """Raise or clear the one issue this integration can fix itself."""
    if not conflicts:
        ir.async_delete_issue(hass, DOMAIN, STATISTICS_ISSUE)
        return

    ir.async_create_issue(
        hass,
        DOMAIN,
        STATISTICS_ISSUE,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=STATISTICS_ISSUE,
        translation_placeholders={"count": str(conflicts)},
        learn_more_url=LEARN_MORE,
    )


async def async_create_fix_flow(hass: HomeAssistant, issue_id: str, data):
    """Build the guided fix. Only the statistics issue has one."""
    return StatisticsUnitsRepairFlow()


class StatisticsUnitsRepairFlow(RepairsFlow):
    """Confirm, then rewrite the statistics units that disagree."""

    async def async_step_init(self, user_input=None) -> FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input=None) -> FlowResult:
        if user_input is not None:
            from .statistics import async_fix_statistics_units

            fixed = await async_fix_statistics_units(self.hass)
            _LOGGER.info("Repaired %d statistics unit(s)", len(fixed))
            return self.async_create_entry(title="", data={})

        return self.async_show_form(step_id="confirm", data_schema=None)
