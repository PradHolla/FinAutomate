"""Replay contract tests.

No browser. Parameter binding, secret handling and the result contract are all pure,
which is deliberate: they are the parts a caller depends on, so they should be
checkable without a running application.
"""

from pathlib import Path

import pytest
from pydantic import SecretStr

from finautomate.artifact import load_capability
from finautomate.replay import ParameterError, bind, dry_run, render
from finautomate.result import EXIT_CODES, BusinessOutcome, HardFailure, NeedsHuman, Success

REFERENCE = Path(__file__).parent.parent / "artifacts" / "open_new_account.yaml"
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
