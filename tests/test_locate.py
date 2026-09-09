"""Locator resolution tests.

No browser. The snapshots below are handmade copies of what ParaBank actually
returns, taken from the Phase 0 probe, so the cases are real even though nothing
is running.
"""

import pytest

from finautomate.artifact import AnchoredRole, FieldId, FieldName, LocatorBundle, RoleName
from finautomate.locate import explain, normalize, resolve, text_matches, text_present
from finautomate.surface.models import Control, Snapshot, TextAnchor


def ctl(ref: str, role: str, order: int, **kw: object) -> Control:
    return Control(ref=ref, role=role, doc_order=order, **kw)  # type: ignore[arg-type]


@pytest.fixture
def login_page() -> Snapshot:
    """ParaBank's login panel. Both text boxes have an empty accessible name -
    the app puts "Username" in a <b> tag with no <label>."""
    return Snapshot(
        url="/parabank/index.htm",
        title="ParaBank | Welcome",
        anchors=[
            TextAnchor(text="Customer Login", doc_order=0),
            TextAnchor(text="Username", doc_order=1),
            TextAnchor(text="Password", doc_order=3),
        ],
        controls=[
            ctl("c1", "textbox", 2, field_name="username"),
            ctl("c2", "textbox", 4, field_name="password"),
            ctl("c3", "button", 5, name="Log In"),
            ctl("c4", "link", 6, name="Register", text="Register"),
        ],
    )


@pytest.fixture
def open_account_page() -> Snapshot:
    """The open-account form. Note "Open New Account" is the name of BOTH the nav
    link and the submit button - a real collision from the running app."""
    return Snapshot(
        url="/parabank/openaccount.htm",
        title="ParaBank | Open Account",
        anchors=[
            TextAnchor(text="Open New Account", doc_order=0),
            TextAnchor(text="What type of Account would you like to open?", doc_order=2),
            TextAnchor(
                text="A minimum of $100.00 must be deposited into this account at time of opening.",
                doc_order=4,
            ),
        ],
        controls=[
            ctl("n1", "link", 1, name="Open New Account", text="Open New Account"),
            ctl("s1", "combobox", 3, field_id="type", options=["CHECKING", "SAVINGS"]),
            ctl("s2", "combobox", 5, field_id="fromAccountId", options=["12345", "54321"]),
            ctl("b1", "button", 6, name="Open New Account"),
        ],
    )


# -- text matching ----------------------------------------------------------


def test_normalize_folds_case_and_collapses_whitespace() -> None:
    assert normalize("  Log   In\n") == "log in"


@pytest.mark.parametrize(
    ("candidate", "wanted", "mode", "expected"),
    [
        ("Log In", "log in", "exact", True),
        ("Log In", "Log", "exact", False),
        ("A minimum of $100.00 must be deposited", "A minimum of", "prefix", True),
        ("A minimum of $250.00 must be deposited", "A minimum of", "prefix", True),
        ("A minimum of $100.00 must be deposited", "must be deposited", "contains", True),
    ],
)
def test_text_matches(candidate: str, wanted: str, mode: str, expected: bool) -> None:
    assert text_matches(candidate, wanted, mode) is expected  # type: ignore[arg-type]


def test_prefix_match_survives_a_changed_config_value() -> None:
    """The reason `prefix` exists. That dollar figure is an admin setting, so an
    exact-match anchor would break the day an institution changed it."""
    at_100 = "A minimum of $100.00 must be deposited into this account"
    at_500 = "A minimum of $500.00 must be deposited into this account"
    assert text_matches(at_100, "A minimum of", "prefix")
    assert text_matches(at_500, "A minimum of", "prefix")
    assert not text_matches(at_500, at_100, "exact")


# -- the ladder -------------------------------------------------------------


def test_role_name_resolves_the_button(login_page: Snapshot) -> None:
    bundle = LocatorBundle(
        description="the Log In button",
        strategies=[RoleName(kind="role_name", role="button", name="Log In")],
    )
    got = resolve(bundle, login_page)
    assert got.found
    assert got.control is not None and got.control.ref == "c3"
    assert got.strategy_index == 0
    assert not got.used_fallback


def test_unlabeled_field_needs_the_anchor(login_page: Snapshot) -> None:
    """Tier 1 cannot address this field at all: its accessible name is empty."""
    by_name = LocatorBundle(
        description="the Username field",
        strategies=[RoleName(kind="role_name", role="textbox", name="Username")],
    )
    assert not resolve(by_name, login_page).found

    by_anchor = LocatorBundle(
        description="the Username field",
        strategies=[AnchoredRole(kind="anchored_role", role="textbox", anchor="Username")],
    )
    got = resolve(by_anchor, login_page)
    assert got.control is not None and got.control.field_name == "username"


def test_anchor_picks_the_nearest_not_the_first(login_page: Snapshot) -> None:
    """Two identical textboxes on the page. The Password anchor must reach the
    second one, not the first."""
    bundle = LocatorBundle(
        description="the Password field",
        strategies=[AnchoredRole(kind="anchored_role", role="textbox", anchor="Password")],
    )
    got = resolve(bundle, login_page)
    assert got.control is not None and got.control.field_name == "password"


def test_role_disambiguates_a_shared_name(open_account_page: Snapshot) -> None:
    """ "Open New Account" names both a link and a button. Name alone is a coin
    flip; role plus name is exact."""
    link = resolve(
        LocatorBundle(
            description="the nav link",
            strategies=[RoleName(kind="role_name", role="link", name="Open New Account")],
        ),
        open_account_page,
    )
    button = resolve(
        LocatorBundle(
            description="the submit button",
            strategies=[RoleName(kind="role_name", role="button", name="Open New Account")],
        ),
        open_account_page,
    )
    assert link.control is not None and link.control.ref == "n1"
    assert button.control is not None and button.control.ref == "b1"


def test_falls_through_to_the_next_strategy(open_account_page: Snapshot) -> None:
    bundle = LocatorBundle(
        description="the account type dropdown",
        strategies=[
            AnchoredRole(kind="anchored_role", role="combobox", anchor="Nothing Like This"),
            FieldId(kind="field_id", id="type"),
        ],
    )
    got = resolve(bundle, open_account_page)
    assert got.control is not None and got.control.field_id == "type"
    assert got.strategy_index == 1
    assert got.used_fallback, "a fallback catching the miss is the drift signal"
    assert got.attempts[0].note == "no match"


def test_ambiguity_is_a_failure_not_a_guess(open_account_page: Snapshot) -> None:
    """Two buttons share a name. Picking either would be how automation quietly
    does the wrong thing, so the strategy resolves nothing."""
    page = open_account_page.model_copy(
        update={
            "controls": [
                *open_account_page.controls,
                ctl("b2", "button", 7, name="Open New Account"),
            ]
        }
    )
    bundle = LocatorBundle(
        description="the submit button",
        strategies=[RoleName(kind="role_name", role="button", name="Open New Account")],
    )
    got = resolve(bundle, page)
    assert not got.found
    assert got.attempts[0].note == "ambiguous, 2 matches"


def test_repeated_anchor_pointing_at_different_controls_is_ambiguous() -> None:
    """A transfer form with two "Amount" labels, each above its own box. One
    anchor text, two candidates, so the strategy must decline."""
    page = Snapshot(
        url="/x",
        title="x",
        anchors=[TextAnchor(text="Amount", doc_order=0), TextAnchor(text="Amount", doc_order=2)],
        controls=[ctl("a1", "textbox", 1), ctl("a2", "textbox", 3)],
    )
    got = resolve(
        LocatorBundle(
            description="the amount field",
            strategies=[AnchoredRole(kind="anchored_role", role="textbox", anchor="Amount")],
        ),
        page,
    )
    assert not got.found
    assert "ambiguous" in got.attempts[0].note


def test_repeated_anchor_pointing_at_the_same_control_still_resolves() -> None:
    """Both anchors reach the same box, so there is no real ambiguity."""
    page = Snapshot(
        url="/x",
        title="x",
        anchors=[TextAnchor(text="Amount", doc_order=0), TextAnchor(text="Amount", doc_order=1)],
        controls=[ctl("a1", "textbox", 2)],
    )
    got = resolve(
        LocatorBundle(
            description="the amount field",
            strategies=[AnchoredRole(kind="anchored_role", role="textbox", anchor="Amount")],
        ),
        page,
    )
    assert got.control is not None and got.control.ref == "a1"


def test_nothing_found_records_every_attempt(login_page: Snapshot) -> None:
    bundle = LocatorBundle(
        description="a button that is not there",
        strategies=[
            RoleName(kind="role_name", role="button", name="Delete Everything"),
            FieldName(kind="field_name", name="nope"),
        ],
    )
    got = resolve(bundle, login_page)
    assert not got.found
    assert len(got.attempts) == 2
    message = explain(bundle, got)
    assert "a button that is not there" in message
    assert "role_name" in message and "field_name" in message


def test_field_name_is_matched_exactly(login_page: Snapshot) -> None:
    """Attribute values are identifiers, not prose. Case-folding them would be wrong."""
    assert resolve(
        LocatorBundle(
            description="username",
            strategies=[FieldName(kind="field_name", name="username")],
        ),
        login_page,
    ).found
    assert not resolve(
        LocatorBundle(
            description="username",
            strategies=[FieldName(kind="field_name", name="USERNAME")],
        ),
        login_page,
    ).found


# -- text checkpoints -------------------------------------------------------


def test_text_present_searches_anchors_and_controls(login_page: Snapshot) -> None:
    assert text_present("Customer Login", "exact", login_page)
    assert text_present("customer", "contains", login_page)
    assert not text_present("Account Opened", "contains", login_page)
