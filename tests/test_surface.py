"""Tests for the browser `Surface` driver.

Run against a static fixture rather than live ParaBank, so they need nothing but
a headless Chromium - no Docker, no network. The fixture reproduces the exact
patterns found in ParaBank: bold text standing in for a `<label>`, a name
collision between a nav link and a submit button, and a hidden input plus a
`display:none` element that both need to be excluded.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, sync_playwright

from finautomate.surface.browser import (
    BrowserSurface,
    ControlNotFoundError,
    OptionNotFoundError,
)
from finautomate.surface.models import Control, Snapshot

FIXTURE = Path(__file__).parent / "fixtures" / "login_like.html"


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        instance = p.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    pg = browser.new_page()
    pg.goto(f"file://{FIXTURE.resolve()}")
    yield pg
    pg.close()


@pytest.fixture
def surface(page: Page) -> BrowserSurface:
    # base_url is unused by these tests - none of them navigate - but the
    # constructor requires one, so point it at the fixture's own directory.
    return BrowserSurface(page, base_url=f"file://{FIXTURE.parent.resolve()}")


@pytest.fixture
def snapshot(surface: BrowserSurface) -> Snapshot:
    return surface.observe()


def by_field_name(snapshot: Snapshot, field_name: str) -> Control:
    return next(c for c in snapshot.controls if c.field_name == field_name)


# -- extraction ---------------------------------------------------------------


def test_unlabeled_inputs_have_no_name(snapshot: Snapshot) -> None:
    """Unlabeled is correct, not a bug - ParaBank has 36 inputs like this."""
    assert by_field_name(snapshot, "username").name == ""
    assert by_field_name(snapshot, "password").name == ""


def test_submit_button_name_comes_from_its_value_attribute(snapshot: Snapshot) -> None:
    login = next(c for c in snapshot.controls if c.value == "Log In")
    assert login.name == "Log In"
    assert login.role == "button"


def test_name_collision_between_nav_link_and_submit_button(snapshot: Snapshot) -> None:
    matches = [c for c in snapshot.controls if c.name == "Open New Account"]
    roles = {c.role for c in matches}
    assert roles == {"link", "button"}


def test_select_reports_combobox_role_and_visible_option_labels(snapshot: Snapshot) -> None:
    select_control = next(c for c in snapshot.controls if c.field_id == "type")
    assert select_control.role == "combobox"
    assert select_control.options == ["CHECKING", "SAVINGS"]


def test_hidden_input_and_display_none_element_are_excluded(snapshot: Snapshot) -> None:
    assert all(c.field_name != "csrf" for c in snapshot.controls)
    assert all("This is not shown" not in a.text for a in snapshot.anchors)


def test_anchor_precedes_the_field_it_labels(snapshot: Snapshot) -> None:
    username_anchor = next(a for a in snapshot.anchors if a.text == "Username")
    username_input = by_field_name(snapshot, "username")
    assert username_anchor.doc_order < username_input.doc_order


# -- actions --------------------------------------------------------------------


def test_select_by_label_then_read_round_trips(surface: BrowserSurface, snapshot: Snapshot) -> None:
    select_ref = next(c for c in snapshot.controls if c.field_id == "type").ref
    surface.select(select_ref, "SAVINGS")
    assert surface.read(select_ref) == "SAVINGS"


def test_click_with_unknown_ref_raises_control_not_found(surface: BrowserSurface) -> None:
    with pytest.raises(ControlNotFoundError):
        surface.click("no-such-ref")


def test_select_with_unknown_label_raises_option_not_found(
    surface: BrowserSurface, snapshot: Snapshot
) -> None:
    select_ref = next(c for c in snapshot.controls if c.field_id == "type").ref
    with pytest.raises(OptionNotFoundError):
        surface.select(select_ref, "MONEY MARKET")
