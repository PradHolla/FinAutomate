"""Replay contract tests.

No browser. Parameter binding, secret handling and the result contract are all pure,
which is deliberate: they are the parts a caller depends on, so they should be
checkable without a running application.
"""

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from finautomate.artifact import (
    Capability,
    Outcome,
    Recovery,
    Step,
    Target,
    TextVisible,
    load_capability,
)
from finautomate.replay import (
    ParameterError,
    bind,
    detected,
    dry_run,
    render,
    with_runtime_outcomes,
)
from finautomate.result import EXIT_CODES, BusinessOutcome, HardFailure, NeedsHuman, Success
from finautomate.surface.models import Control, Snapshot, TextAnchor

REFERENCE = Path(__file__).parent / "fixtures" / "reference_capability.yaml"
ARGS = {
    "username": "john",
    "password": "demo",
    "account_type": "SAVINGS",
    "funding_account_id": "12345",
}


@pytest.fixture
def capability():  # type: ignore[no-untyped-def]
    return load_capability(REFERENCE)


# -- parameters are checked before anything happens -------------------------


def test_missing_required_parameter_is_refused(capability) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ParameterError, match="requires"):
        bind(capability, {"username": "john"})


def test_unknown_parameter_is_refused(capability) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ParameterError, match="no parameter named"):
        bind(capability, {**ARGS, "nonsense": "x"})


def test_value_outside_a_declared_enum_is_refused(capability) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ParameterError, match="not one of"):
        bind(capability, {**ARGS, "account_type": "CRYPTO"})


def test_dry_run_validates_without_touching_a_browser(capability) -> None:  # type: ignore[no-untyped-def]
    lines = dry_run(capability, ARGS)
    assert any("RISKY" in line for line in lines), "the irreversible step must be visible"
    with pytest.raises(ParameterError):
        dry_run(capability, {"username": "john"})


# -- secrets ----------------------------------------------------------------


def test_secret_input_is_wrapped(capability) -> None:  # type: ignore[no-untyped-def]
    bound = bind(capability, ARGS)
    assert isinstance(bound["password"], SecretStr)
    assert not isinstance(bound["username"], SecretStr)


def test_a_wrapped_secret_does_not_print_itself(capability) -> None:  # type: ignore[no-untyped-def]
    """The reason for the wrapper. Anything that formats this object - a log line,
    an exception, a debugger - gets the mask, not the password."""
    bound = bind(capability, ARGS)
    assert "demo" not in repr(bound)
    assert "demo" not in str(bound["password"])


def test_rendering_unwraps_only_at_the_point_of_use(capability) -> None:  # type: ignore[no-untyped-def]
    bound = bind(capability, ARGS)
    assert render("{{password}}", bound) == "demo"
    assert render("{{username}}", bound) == "john"
    assert render("no placeholders here", bound) == "no placeholders here"
    assert render("{{unknown}}", bound) == "{{unknown}}"


# -- the result contract ----------------------------------------------------


def test_the_four_outcomes_have_distinct_exit_codes() -> None:
    """A business outcome is not success and not a crash.

    Zero would tell a caller the job was done. One would page somebody. Two says
    the application answered and the answer was no, which is the thing the brief
    warns is most often got wrong.
    """
    assert EXIT_CODES == {
        "success": 0,
        "hard_failure": 1,
        "business_outcome": 2,
        "needs_human": 3,
    }
    assert len(set(EXIT_CODES.values())) == 4


def test_a_business_outcome_is_not_shaped_like_an_error() -> None:
    """It carries an answer, not a diagnosis. A caller cannot mistake one for the
    other, because neither type has the other's fields."""
    outcome = BusinessOutcome(
        capability="c", version=1, outcome="NO_SUCH_MEMBER", message="No such member.", step="s"
    )
    assert not hasattr(outcome, "screenshot")
    assert not hasattr(outcome, "expected")

    failure = HardFailure(
        capability="c", version=1, step="s", expected="a button", observed="nothing"
    )
    assert not hasattr(failure, "outcome")


def test_a_hard_failure_says_what_was_expected_and_what_was_seen() -> None:
    failure = HardFailure(
        capability="c",
        version=1,
        step="submit",
        expected="the Log In button",
        observed="no match at any strategy",
        screenshot="evidence/x/failed-submit.png",
    )
    assert failure.step and failure.expected and failure.observed and failure.screenshot


def test_drift_is_reported_even_when_the_run_succeeds() -> None:
    """A fallback locator catching a miss is not an error, but it is the earliest
    warning that the page has moved. Silence here means finding out later."""
    from finautomate.result import StepRecord

    ok = Success(
        capability="c",
        version=1,
        steps=[
            StepRecord(id="a", action="click", strategy_index=0, used_fallback=False),
            StepRecord(id="b", action="click", strategy_index=2, used_fallback=True),
        ],
    )
    assert ok.drifting_steps == ["b"]


def test_needs_human_names_the_step_it_stopped_at() -> None:
    held = NeedsHuman(capability="c", version=1, step="submit_payment", reason="irreversible")
    assert held.step == "submit_payment"


def test_a_captured_human_action_can_be_logged(tmp_path: Path) -> None:
    """Regression. Actions arrive as {"kind": "click", ...}, and the evidence
    writer's first positional argument is also called `kind`. Splatting the dict
    collided with it and crashed the run at the moment a person had just finished
    the step - the worst possible time, because their work was already done."""
    from finautomate.evidence import Evidence

    evidence = Evidence(tmp_path, "run-1", frozenset({"hunter2"}))
    action = {"kind": "click", "target": 'input "Open New Account"', "value": "hunter2"}
    evidence.event(
        "human_action",
        did=action["kind"],
        target=action["target"],
        value=action["value"],
    )
    written = (tmp_path / "run-1" / "run.jsonl").read_text()
    assert '"did": "click"' in written
    assert "hunter2" not in written, "secrets must be redacted from the audit trail too"


# -- classifying what is on screen ------------------------------------------


def error_page() -> Snapshot:
    """What ParaBank serves when a signed-in page is asked for without a session.

    Copied from the running app, and the reason this test exists: it is the error
    page *and* it still carries the login panel in the left column. Two declared
    conditions match the same screen, so which one wins is a real decision.
    """
    return Snapshot(
        url="/parabank/openaccount.htm",
        title="ParaBank | Error",
        anchors=[
            TextAnchor(text="Customer Login", doc_order=0),
            TextAnchor(text="Username", doc_order=1),
            TextAnchor(text="Password", doc_order=3),
            TextAnchor(text="An internal error has occurred and has been logged.", doc_order=6),
        ],
        controls=[
            Control(ref="c1", role="textbox", doc_order=2, field_name="username"),
            Control(ref="c2", role="textbox", doc_order=4, field_name="password"),
            Control(ref="c3", role="button", doc_order=5, name="Log In"),
        ],
    )


def test_an_expired_session_is_spotted_on_the_error_page(capability) -> None:  # type: ignore[no-untyped-def]
    found = detected(capability.outcomes, "recoverable", error_page())
    assert found is not None and found.name == "SESSION_EXPIRED"
    assert found.recovery is not None and found.recovery.max_attempts == 1


def test_a_declared_hard_failure_is_named_rather_than_guessed(capability) -> None:  # type: ignore[no-untyped-def]
    """Before this, a capability could declare "if you see this text the app has
    broken" and nothing read it. The report said only that the expected thing was
    missing, which points the reader at us instead of at the application."""
    found = detected(capability.outcomes, "hard_failure", error_page())
    assert found is not None and found.name == "APP_ERROR"


def test_a_clean_page_matches_nothing(capability) -> None:  # type: ignore[no-untyped-def]
    healthy = Snapshot(
        url="/parabank/openaccount.htm",
        title="ParaBank | Open Account",
        anchors=[TextAnchor(text="What type of Account would you like to open?", doc_order=0)],
        controls=[Control(ref="s1", role="combobox", doc_order=1, field_id="type")],
    )
    assert detected(capability.outcomes, "recoverable", healthy) is None
    assert detected(capability.outcomes, "hard_failure", healthy) is None


def test_a_recoverable_outcome_must_say_how_to_recover() -> None:
    """Recoverable with no recovery block reads fine and cannot work: the engine
    would detect it, classify it as retryable, and then quietly fall through to a
    hard failure. Rejected at load time so the engine never has to ask."""
    with pytest.raises(ValidationError, match="declares no recovery"):
        Outcome(
            name="SESSION_EXPIRED",
            classification="recoverable",
            detect=TextVisible(kind="text_visible", text="Customer Login"),
        )
    with pytest.raises(ValidationError, match="declares no detector"):
        Outcome(
            name="SESSION_EXPIRED",
            classification="recoverable",
            recovery=Recovery(action="restart"),
        )


# -- the app's runtime conditions come from tenant config -------------------


def runtime_outcomes() -> list[Outcome]:
    return [
        Outcome(
            name="SESSION_EXPIRED",
            classification="recoverable",
            message="from config",
            detect=TextVisible(kind="text_visible", text="Customer Login"),
            recovery=Recovery(action="restart"),
        ),
        Outcome(
            name="APP_ERROR",
            classification="hard_failure",
            message="from config",
            detect=TextVisible(kind="text_visible", text="An internal error"),
        ),
    ]


def test_config_outcomes_are_added_to_a_recording_that_lacks_them() -> None:
    """The case that matters. A discovery run can only record what it saw, and on a
    healthy application nothing goes wrong - so no recording will ever declare a
    session timeout. It has to come from somewhere the model is not."""
    recorded = Capability(
        id="c",
        version=1,
        title="t",
        description="a capability that recorded no runtime conditions",
        target=Target(app="parabank", entry="/parabank/index.htm"),
        steps=[Step(id="s", action="navigate")],
        success=TextVisible(kind="text_visible", text="done"),
        outcomes=[],
    )
    merged = with_runtime_outcomes(recorded, runtime_outcomes())
    assert [o.name for o in merged.outcomes] == ["SESSION_EXPIRED", "APP_ERROR"]
    assert recorded.outcomes == [], "the original must not be mutated"


def test_the_capability_wins_a_name_it_already_declares(capability) -> None:  # type: ignore[no-untyped-def]
    """What a capability recorded about its own screens is more specific than a
    blanket rule about the application, so it is kept.

    Config declares one name the capability already has and one it does not, and both
    halves are checked in the same call. An earlier version of this test only passed
    names the capability already declared, so the function returned its input and the
    assertions held whether the merge ran or not - it would have passed against a
    `return capability` stub. One skip and one addition in the same call is what
    forces the logic to actually execute.
    """
    fresh = Outcome(
        name="MAINTENANCE_WINDOW",
        classification="hard_failure",
        message="from config",
        detect=TextVisible(kind="text_visible", text="down for maintenance"),
    )
    merged = with_runtime_outcomes(capability, [*runtime_outcomes(), fresh])

    session = next(o for o in merged.outcomes if o.name == "SESSION_EXPIRED")
    assert session.message != "from config", "config must not overwrite the capability"
    assert "MAINTENANCE_WINDOW" in {o.name for o in merged.outcomes}, "the new name must arrive"
    assert len(merged.outcomes) == len(capability.outcomes) + 1, "exactly one added, two skipped"


def test_no_config_outcomes_returns_the_capability_untouched(capability) -> None:  # type: ignore[no-untyped-def]
    """Identity, not equality. A tenant with nothing to declare must not pay for a
    copy of every capability it loads.

    Worth saying plainly: this one cannot catch a merge that does nothing, because a
    function that ignored its second argument entirely would satisfy it too. It
    guards the opposite mistake - an implementation that copies unconditionally. The
    two tests above are the ones that hold the merge itself honest.
    """
    assert with_runtime_outcomes(capability, []) is capability
