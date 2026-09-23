"""Let the web UI ask the poll loop to rediscover points.

The add-on runs two processes: a Flask UI and a polling loop that owns the
oBIX connection. They already coordinate through the filesystem — the loop
watches points.yaml for changes — so a rescan request is one more file
rather than a new channel.

A rescan is needed whenever the station itself changes: points added in
Niagara do not appear here until something goes looking, and until now that
meant waiting for a reconnect or restarting the add-on.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger("niagara-ha.rescan")

REQUEST_FILE = Path("/config/niagara-ha/rescan.request")


def request_rescan(reason: str = "") -> float:
    """Ask for a rediscovery. Returns when it was asked for."""
    now = time.time()
    try:
        REQUEST_FILE.parent.mkdir(parents=True, exist_ok=True)
        REQUEST_FILE.write_text(f"{now}\n{reason}\n")
    except OSError as err:
        logger.warning("Could not request a rescan: %s", err)
        return 0.0
    logger.info("Rescan requested%s", f": {reason}" if reason else "")
    return now


def pending() -> float:
    """When a rescan was requested, or 0. Does not consume the request."""
    try:
        first = REQUEST_FILE.read_text().splitlines()[0]
        return float(first)
    except (OSError, IndexError, ValueError):
        return 0.0


def take_request() -> bool:
    """Consume a pending request. True if there was one.

    Deleting before acting rather than after: a rescan that crashes should
    not leave a request that triggers forever.
    """
    if not REQUEST_FILE.exists():
        return False
    try:
        REQUEST_FILE.unlink()
    except OSError as err:
        logger.warning("Could not clear the rescan request: %s", err)
        return False
    return True
