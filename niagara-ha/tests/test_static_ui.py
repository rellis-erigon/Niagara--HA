"""Invariants in the single-page UI that Python tests can still hold.

There is no browser here, so these are deliberately narrow: they guard
the couple of places where a wrong selector silently breaks a save rather
than throwing anything anyone would see.
"""
import re
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[1] / "static" / "index.html"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text()


def test_slot_bindings_are_harvested_only_from_slot_controls(page):
    """`slot-select` is a styling class shared by inputs across several
    dialogs. Harvesting all of them posted the faceplate picker as a
    binding named "undefined", the add-on rejected the whole request, and
    nothing saved — with a toast that did not say why."""
    harvests = re.findall(
        r"querySelectorAll\('(\.slot-select[^']*)'\)\.forEach\(sel => \{\s*"
        r"bindings\[", page)
    assert harvests, "the binding harvest was renamed; update this test"
    for selector in harvests:
        assert "[data-slot]" in selector, (
            f"{selector} picks up controls that are not slots")


def test_the_faceplate_picker_is_not_a_slot(page):
    """It must not carry data-slot, or it becomes a binding again."""
    picker = re.search(r"<select[^>]*id=\"faceplate-select\"[^>]*>", page)
    assert picker, "the faceplate picker is gone"
    assert "data-slot" not in picker.group(0)


def test_api_failures_carry_the_server_reason(page):
    """The API says why it refused. Throwing the status text alone turned
    every failure into the caller's generic message."""
    helper = re.search(r"async function api\(path, opts\) \{.*?\n\}", page, re.S)
    assert helper, "the api helper was renamed; update this test"
    assert "body.error" in helper.group(0)


def test_the_history_screen_is_reachable_from_the_nav(page):
    """A screen with no nav button is a screen nobody can open."""
    assert 'data-view="histories"' in page
    assert 'id="view-histories"' in page
    assert "if (view === 'histories') loadHistories();" in page


def test_the_history_screen_hides_with_the_others(page):
    """Every view's display is set on each switch; a view left out stays
    visible underneath whichever screen you move to."""
    switch = re.search(
        r"document\.querySelectorAll\('\.viewnav \.btn'\).*?\n\}\);", page, re.S)
    assert switch, "the view switch was renamed; update this test"
    for view in ("points", "devices", "diagnostics", "histories"):
        assert f"$('#view-{view}').style.display" in switch.group(0), view


def test_every_control_the_history_screen_binds_to_exists(page):
    """A wrong id here fails silently: addEventListener on null throws once
    at load and the rest of the screen's handlers never attach."""
    bound = set(re.findall(r"\$\('#(hist-[a-z-]+)'\)", page))
    assert bound, "the history handlers were renamed; update this test"
    for element_id in bound:
        assert f'id="{element_id}"' in page, f"#{element_id} is bound but absent"


def test_history_rows_are_handled_by_delegation(page):
    """The rows are replaced on every load, so per-row listeners would have
    to be reattached each time and quietly stop working once they are not."""
    assert "$('#view-histories').addEventListener('click'" in page
    assert "$('#view-histories').addEventListener('change'" in page


def test_the_master_switch_posts_to_the_add_on(page):
    screen = page[page.index("// -- History screen"):]
    assert "/api/histories/enabled" in screen
    assert "/api/histories/add" in screen
    assert "/api/histories/remove" in screen
    assert "/api/histories/toggle" in screen


def test_the_history_screen_says_when_syncing_is_off(page):
    """Paired trends that are not importing must not look like they are."""
    screen = page[page.index("// -- History screen"):]
    assert "master switch above is off" in screen


def test_history_values_are_escaped(page):
    """A Niagara history name can contain anything, and it is rendered into
    HTML via innerHTML."""
    screen = page[page.index("function renderHistSelected"):
                  page.index("async function histPreview")]
    for interpolation in re.findall(r"\$\{([^}]+)\}", screen):
        assert (
            "esc(" in interpolation
            or "histTime(" in interpolation
            or interpolation.strip().startswith(("device", "point", "synced",
                                                 "evidence[", "h.enabled",
                                                 "h.count ==", "addable"))
        ), interpolation


def test_faceplate_options_are_built_from_what_the_face_declares(page):
    """Hardcoding the inputs would mean editing the UI for every new
    parametric faceplate. They come from the catalogue instead."""
    block = page[page.index("function renderFaceplateOptions"):
                 page.index("const faceSelect")]
    assert "face.options" in block
    assert "o.label || o.key" in block
    assert 'data-face-opt="${esc(o.key)}"' in block


def test_faceplate_option_inputs_carry_the_declared_bounds(page):
    block = page[page.index("function renderFaceplateOptions"):
                 page.index("const faceSelect")]
    assert 'min="${esc(String(o.min))}"' in block
    assert 'max="${esc(String(o.max))}"' in block


def test_a_saved_faceplate_option_wins_over_the_default(page):
    """Reopening the dialog must show what the device is set to, not the
    faceplate's default."""
    block = page[page.index("function renderFaceplateOptions"):
                 page.index("const faceSelect")]
    assert "savedOptions[o.key] !== undefined" in block


def test_the_options_rerender_when_the_face_changes(page):
    """Each face declares its own; leaving the old inputs up would post a
    door count to a tank."""
    assert "faceSelect.addEventListener('change', renderFaceplateOptions)" in page


def test_faceplate_options_are_posted_with_the_bindings(page):
    assert "faceplate_options: collectFaceplateOptions()" in page


def test_a_blank_option_box_is_omitted_rather_than_sent_as_zero(page):
    """For a door count, zero is a cabinet with no front."""
    block = page[page.index("function collectFaceplateOptions"):
                 page.index("$('#slots-save')")]
    assert "input.value !== ''" in block
    assert "Number.isFinite(value)" in block


def test_the_option_inputs_are_not_harvested_as_slot_bindings(page):
    """`slot-select` once swept up the faceplate picker and posted it as a
    binding called "undefined", and nothing saved."""
    block = page[page.index("function renderFaceplateOptions"):
                 page.index("const faceSelect")]
    assert "slot-select" not in block
    assert "data-slot" not in block
