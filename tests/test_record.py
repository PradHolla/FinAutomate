"""Recorder tests.

The snapshots here are the real ParaBank screens, transcribed from the Phase 0
probe. If the recorder can produce a working bundle for every control in the actual
flow, discovery will too.
"""

import pytest

from finautomate.artifact import AnchoredRole, FieldName, RoleName
from finautomate.locate import resolve
from finautomate.record import build_bundle
from finautomate.surface.models import Control, Snapshot, TextAnchor


def ctl(ref: str, role: str, order: int, **kw: object) -> Control:
    return Control(ref=ref, role=role, doc_order=order, **kw)  # type: ignore[arg-type]


@pytest.fixture
def login_page() -> Snapshot:
    return Snapshot(
        url="/parabank/index.htm",
        title="ParaBank",
        anchors=[
            TextAnchor(text="Customer Login", doc_order=0),
            TextAnchor(text="Username", doc_order=1),
            TextAnchor(text="Password", doc_order=3),
        ],
        controls=[
            ctl("c1", "textbox", 2, field_name="username"),
            ctl("c2", "textbox", 4, field_name="password"),
            ctl("c3", "button", 5, name="Log In"),
        ],
    )


@pytest.fixture
def open_account_page() -> Snapshot:
    return Snapshot(
        url="/parabank/openaccount.htm",
        title="ParaBank",
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
            ctl("s2", "combobox", 5, field_id="fromAccountId", options=["12345"]),
            ctl("b1", "button", 6, name="Open New Account"),
        ],
    )


# -- the property that matters ---------------------------------------------


def test_every_control_in_the_real_flow_can_be_recorded(
    login_page: Snapshot, open_account_page: Snapshot
) -> None:
    """Nothing in the flow is unaddressable. If this fails, discovery cannot
    produce a working artifact for that screen."""
    for page in (login_page, open_account_page):
        for control in page.controls:
            bundle = build_bundle(control, page, f"the {control.ref}")
            assert bundle is not None, f"{control.ref} could not be recorded"


def test_recorded_bundles_resolve_back_to_the_same_control(
    login_page: Snapshot, open_account_page: Snapshot
) -> None:
    """Record then resolve is a round trip. Every strategy kept must find the
    control it was built from, and only that one."""
    for page in (login_page, open_account_page):
        for control in page.controls:
            bundle = build_bundle(control, page, "x")
            assert bundle is not None
            for index in range(len(bundle.strategies)):
                single = bundle.model_copy(update={"strategies": [bundle.strategies[index]]})
                outcome = resolve(single, page)
                assert outcome.control is not None
                assert outcome.control.ref == control.ref, (
                    f"{control.ref} strategy {index} ({bundle.strategies[index].kind}) "
                    f"resolved to {outcome.control.ref}"
                )


# -- ordering ---------------------------------------------------------------


def test_named_control_prefers_role_and_name(login_page: Snapshot) -> None:
    bundle = build_bundle(login_page.control("c3"), login_page, "the Log In button")
    assert bundle is not None
    assert isinstance(bundle.strategies[0], RoleName)
    assert bundle.strategies[0].name == "Log In"


def test_unlabeled_field_falls_to_an_anchor_then_the_field_name(login_page: Snapshot) -> None:
    """No accessible name exists, so role+name is never proposed at all."""
    bundle = build_bundle(login_page.control("c1"), login_page, "the Username field")
    assert bundle is not None
    kinds = [s.kind for s in bundle.strategies]
    assert "role_name" not in kinds
    assert isinstance(bundle.strategies[0], AnchoredRole)
    assert bundle.strategies[0].anchor == "Username"
    assert any(isinstance(s, FieldName) and s.name == "username" for s in bundle.strategies)


def test_anchor_chosen_is_the_nearest_one(login_page: Snapshot) -> None:
    """ "Customer Login" also precedes the password box. "Password" is closer and
    is what actually describes it."""
    bundle = build_bundle(login_page.control("c2"), login_page, "the Password field")
    assert bundle is not None
    first = bundle.strategies[0]
    assert isinstance(first, AnchoredRole) and first.anchor == "Password"


# -- the config-value anchor, which is the interesting case -----------------


def test_anchor_containing_a_config_value_is_recorded_as_a_prefix(
    open_account_page: Snapshot,
) -> None:
    """The funding dropdown's anchor embeds $100.00, an administrator setting.
    The recorder must prefer a prefix that stops before the number."""
    bundle = build_bundle(open_account_page.control("s2"), open_account_page, "funding")
    assert bundle is not None
    first = bundle.strategies[0]
    assert isinstance(first, AnchoredRole)
    assert first.match == "prefix"
    assert "100" not in first.anchor
    assert first.anchor == "A minimum of"


def test_that_prefix_still_works_when_the_setting_changes(
    open_account_page: Snapshot,
) -> None:
    """The whole point: re-record nothing, change the bank's minimum, still resolve."""
    bundle = build_bundle(open_account_page.control("s2"), open_account_page, "funding")
    assert bundle is not None

    changed = open_account_page.model_copy(
        update={
            "anchors": [
                a
                if "minimum" not in a.text
                else TextAnchor(
                    text="A minimum of $500.00 must be deposited into this account "
                    "at time of opening.",
                    doc_order=a.doc_order,
                )
                for a in open_account_page.anchors
            ]
        }
    )
    outcome = resolve(bundle, changed)
    assert outcome.control is not None and outcome.control.ref == "s2"
    assert outcome.strategy_index == 0, "the prefix anchor should still be first choice"


def test_short_or_absent_prefix_is_not_proposed() -> None:
    """A two-word anchor truncated before a digit would match half the page. The
    verification step would reject it, but do not waste a slot proposing it."""
    page = Snapshot(
        url="/x",
        title="x",
        anchors=[TextAnchor(text="Acct 12345", doc_order=0)],
        controls=[ctl("a1", "textbox", 1)],
    )
    bundle = build_bundle(page.control("a1"), page, "x")
    assert bundle is not None
    assert all(s.anchor != "Acct" for s in bundle.strategies if isinstance(s, AnchoredRole))


# -- ambiguity is rejected at record time -----------------------------------


def test_ambiguous_strategies_never_reach_the_artifact() -> None:
    """Two buttons share a name. role+name is proposed, fails verification, and is
    dropped - so the artifact never contains a locator that matches two things."""
    page = Snapshot(
        url="/x",
        title="x",
        anchors=[TextAnchor(text="Transfer", doc_order=0)],
        controls=[
            ctl("b1", "button", 1, name="Submit", field_id="first"),
            ctl("b2", "button", 2, name="Submit", field_id="second"),
        ],
    )
    bundle = build_bundle(page.control("b1"), page, "the first Submit")
    assert bundle is not None
    assert all(not isinstance(s, RoleName) for s in bundle.strategies)
    assert resolve(bundle, page).control is not None


def test_unaddressable_control_returns_none() -> None:
    """No name, no id, no text, and no anchor above it. Honest failure beats a
    locator that cannot work."""
    page = Snapshot(url="/x", title="x", anchors=[], controls=[ctl("x1", "link", 0)])
    assert build_bundle(page.control("x1"), page, "mystery") is None


def test_every_tool_maps_to_an_allowed_action() -> None:
    """The model's tool names and the artifact's action names are different
    vocabularies. A tool with no mapping is refused by policy at runtime, which is
    a confusing way to find out. This catches it at test time instead."""
    from finautomate.discover import TOOL_ACTION, _tools
    from finautomate.policy import Policy

    policy = Policy(allowed_path_prefix="/x/")
    for tool in _tools():
        name = tool["name"]
        if name in ("done", "stuck"):
            continue
        assert name in TOOL_ACTION, f"tool {name!r} has no action mapping"
        assert TOOL_ACTION[name] in policy.allowed_actions, f"{name!r} maps outside the allowlist"


def test_customer_data_is_never_recorded_as_a_locator() -> None:
    """A control whose visible text is data must not be addressed by that text.

    Found by reading a real recorded artifact: the funding-account dropdown had a
    text strategy of '1234512456125671267812789...' - every account number this
    customer owns, run together. It resolved correctly on the page it came from,
    which is exactly why verification alone did not catch it. It would match no
    other customer, and it puts one customer's account numbers into a capability
    meant to be reused for all of them.
    """
    page = Snapshot(
        url="/x",
        title="x",
        anchors=[TextAnchor(text="Choose an account", doc_order=0)],
        controls=[
            ctl(
                "s1",
                "combobox",
                1,
                field_id="fromAccountId",
                text="1234512456125671267812789",
                options=["12345", "12456"],
            )
        ],
    )
    bundle = build_bundle(page.control("s1"), page, "funding account")
    assert bundle is not None
    for strategy in bundle.strategies:
        assert strategy.kind != "text", f"recorded customer data: {strategy}"


def test_forbidding_run_data_degrades_rather_than_breaks() -> None:
    """The run-data filter is deliberately blunt, so check what it costs.

    A parameter value can legitimately appear inside a control's name - a button
    called "Open SAVINGS Account" when SAVINGS is the account_type parameter. That
    strategy gets dropped even though it would have worked. The point of this test
    is that the ladder degrades: a lower strategy still resolves, so the step is
    still recordable. If nothing survives, `build_bundle` returns None loudly
    rather than emitting a locator that works exactly once.
    """
    page = Snapshot(
        url="/x",
        title="x",
        anchors=[TextAnchor(text="Account options", doc_order=0)],
        controls=[ctl("b1", "button", 1, name="Open SAVINGS Account", field_id="openBtn")],
    )
    bundle = build_bundle(page.control("b1"), page, "open", frozenset({"SAVINGS"}))
    assert bundle is not None, "a lower strategy should still address it"
    assert all("SAVINGS" not in str(s) for s in bundle.strategies)

    # Nothing but the name to go on: an honest None rather than a one-run locator.
    bare = Snapshot(
        url="/x", title="x", anchors=[], controls=[ctl("b2", "button", 0, name="SAVINGS")]
    )
    assert build_bundle(bare.control("b2"), bare, "x", frozenset({"SAVINGS"})) is None
