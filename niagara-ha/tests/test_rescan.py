"""Tests for the rescan request channel.

Two processes, one filesystem. The web UI asks and the poll loop answers,
which is the same way they already coordinate over points.yaml.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import rescan  # noqa: E402


def setup_function():
    rescan.REQUEST_FILE = Path("/tmp/rescan-test.request")
    if rescan.REQUEST_FILE.exists():
        rescan.REQUEST_FILE.unlink()


def test_nothing_pending_by_default():
    assert rescan.pending() == 0.0
    assert rescan.take_request() is False


def test_a_request_is_visible_then_consumed_once():
    at = rescan.request_rescan("because")
    assert at > 0
    assert rescan.pending() == at
    assert rescan.take_request() is True
    # A second loop iteration must not rediscover all over again.
    assert rescan.take_request() is False
    assert rescan.pending() == 0.0


def test_the_reason_is_kept_but_never_breaks_the_timestamp():
    rescan.request_rescan("a reason with\nnewlines and : colons")
    assert rescan.pending() > 0


def test_a_corrupt_request_file_reads_as_nothing_pending():
    rescan.REQUEST_FILE.write_text("not a timestamp\n")
    assert rescan.pending() == 0.0
    # It is still consumable, so a bad file cannot wedge the loop forever.
    assert rescan.take_request() is True


def test_an_unwritable_location_is_reported_not_raised():
    rescan.REQUEST_FILE = Path("/proc/nope/rescan.request")
    assert rescan.request_rescan() == 0.0
