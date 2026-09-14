"""Replay contract tests.

No browser. Parameter binding, secret handling and the result contract are all pure,
which is deliberate: they are the parts a caller depends on, so they should be
checkable without a running application.
"""

from pathlib import Path
from typing import cast

import pytest
from pydantic import SecretStr, ValidationError

from finautomate.artifact import (
    Capability,
    FieldId,
    LocatorBundle,
    Outcome,
    Recovery,
    RoleName,
    Step,
    Target,
    TextVisible,
    load_capability,
)
from finautomate.evidence import Evidence
from finautomate.replay import (
    ParameterError,
    Replay,
    bind,
    detected,
    dry_run,
    render,
    signature,
    with_runtime_outcomes,
)
from finautomate.result import EXIT_CODES, BusinessOutcome, HardFailure, NeedsHuman, Success
from finautomate.session import Intervention, InterventionStore
from finautomate.surface.browser import BrowserSurface
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


def test_dry_run_with_no_arguments_prints_the_contract(capability) -> None:  # type: ignore[no-untyped-def]
    """Asking what a capability takes must not require already knowing."""
    lines = dry_run(capability, {})
    takes = next(line for line in lines if line.strip().startswith("takes:"))
    for name in ARGS:
        assert name in takes


def test_signature_marks_secrets_and_spells_out_enums(capability) -> None:  # type: ignore[no-untyped-def]
    takes = signature(capability)[0]
    assert "password*" in takes, "a caller has to see which value is a secret"
    assert "account_type=CHECKING|SAVINGS" in takes, "an enum's choices are unguessable"
    assert "username," in takes, "a plain input carries no marker"


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


def test_an_answer_beats_a_retry() -> None:
    """Ordering, and it is the decision this method exists to make.

    A screen that matches both a business outcome and a recoverable condition must
    be read as the answer. "The loan was denied" will not change if we try again,
    and retrying would re-submit a loan application. So `_classify` checks business
    outcomes first. Written as a test because the two checks look interchangeable
    and someone will one day reorder them for tidiness.
    """
    both = [
        Outcome(
            name="LOAN_DENIED",
            classification="business_outcome",
            message="The bank declined the loan request.",
            detect=TextVisible(kind="text_visible", text="Denied", match="exact"),
        ),
        Outcome(
            name="SESSION_EXPIRED",
            classification="recoverable",
            detect=TextVisible(kind="text_visible", text="Denied", match="exact"),
            recovery=Recovery(action="restart"),
        ),
    ]
    screen = Snapshot(
        url="/parabank/requestloan.htm",
        title="ParaBank",
        anchors=[TextAnchor(text="Status:", doc_order=0), TextAnchor(text="Denied", doc_order=1)],
        controls=[],
    )
    assert detected(both, "business_outcome", screen) is not None
    assert detected(both, "recoverable", screen) is not None, "both really do match"

    from finautomate.evidence import Evidence
    from finautomate.replay import Replay

    capability = Capability(
        id="loan",
        version=1,
        title="t",
        description="d",
        target=Target(app="parabank", entry="/parabank/index.htm"),
        steps=[Step(id="apply", action="navigate")],
        success=TextVisible(kind="text_visible", text="Approved"),
        outcomes=both,
    )
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        engine = Replay(capability, None, Evidence(Path(tmp), "r", frozenset()))  # type: ignore[arg-type]
        answer = engine._classify(capability.steps[0], screen)

    assert isinstance(answer, BusinessOutcome), f"expected the answer, got {type(answer).__name__}"
    assert answer.outcome == "LOAN_DENIED"


def test_a_dismiss_recovery_must_say_what_to_dismiss() -> None:
    """`restart` needs nothing: it goes back to the entry point. `dismiss` clicks
    something, so a dismiss with no target is a rule the engine cannot carry out."""
    with pytest.raises(ValidationError, match="what to dismiss"):
        Recovery(action="dismiss")
    with pytest.raises(ValidationError, match="takes no target"):
        Recovery(
            action="restart",
            target=LocatorBundle(
                description="x", strategies=[RoleName(kind="role_name", role="button", name="OK")]
            ),
        )


def test_an_interstitial_is_recoverable_and_a_refusal_is_not() -> None:
    """Two conditions the brief names, kept apart by what you can do about them.

    A notice over an intact page is cleared and the step retried. A refusal cannot be
    retried into success: no argument the caller changes will grant an entitlement, so
    it stops and names itself instead of timing out.
    """
    outcomes = [
        Outcome(
            name="INTERSTITIAL",
            classification="recoverable",
            detect=TextVisible(kind="text_visible", text="Scheduled maintenance"),
            recovery=Recovery(
                action="dismiss",
                target=LocatorBundle(
                    description="the button that closes the notice",
                    strategies=[RoleName(kind="role_name", role="button", name="Continue")],
                ),
            ),
        ),
        Outcome(
            name="PERMISSION_DENIED",
            classification="hard_failure",
            message="not permitted",
            detect=TextVisible(kind="text_visible", text="not authorized"),
        ),
    ]
    covered = Snapshot(
        url="/parabank/openaccount.htm",
        title="ParaBank",
        anchors=[TextAnchor(text="Scheduled maintenance this Sunday", doc_order=0)],
        controls=[Control(ref="b1", role="button", doc_order=1, name="Continue")],
    )
    refused = Snapshot(
        url="/parabank/openaccount.htm",
        title="ParaBank",
        anchors=[TextAnchor(text="You are not authorized to perform this operation", doc_order=0)],
        controls=[],
    )
    assert (found := detected(outcomes, "recoverable", covered)) is not None
    assert found.name == "INTERSTITIAL" and found.recovery is not None
    assert found.recovery.action == "dismiss"
    assert detected(outcomes, "recoverable", refused) is None, "a refusal is not retryable"
    assert (denied := detected(outcomes, "hard_failure", refused)) is not None
    assert denied.name == "PERMISSION_DENIED"


# -- quoting the application, not paraphrasing it ---------------------------


class OneScreen:
    """A surface that shows one screen and can read from it. Enough for an outcome
    that has to look at the page it just landed on."""

    def __init__(self, snapshot: Snapshot) -> None:
        self.snapshot = snapshot

    def observe(self) -> Snapshot:
        return self.snapshot

    def read(self, ref: str) -> str:
        return self.snapshot.control(ref).text

    def navigate(self, path: str) -> None: ...
    def click(self, ref: str) -> None: ...
    def type(self, ref: str, text: str) -> None: ...
    def select(self, ref: str, label: str) -> None: ...


def denied_screen(reason: str) -> Snapshot:
    return Snapshot(
        url="/parabank/requestloan.htm",
        title="Loan Request Processed",
        anchors=[TextAnchor(text="Status:", doc_order=0)],
        controls=[
            Control(ref="r1", role="text", field_id="loanStatus", text="Denied", doc_order=1),
            Control(ref="r2", role="text", field_id="loanRequestDenied", text=reason, doc_order=2),
        ],
    )


def refusal(explain: LocatorBundle | None) -> Outcome:
    return Outcome(
        name="LOAN_DENIED",
        classification="business_outcome",
        message="The bank declined the loan request.",
        detect=TextVisible(kind="text_visible", text="Denied", match="exact"),
        explain=explain,
    )


def loan_capability(outcome: Outcome) -> Capability:
    return Capability(
        id="loan",
        version=1,
        title="t",
        description="d",
        target=Target(app="parabank", entry="/parabank/index.htm"),
        steps=[Step(id="apply", action="navigate")],
        success=TextVisible(kind="text_visible", text="Approved"),
        outcomes=[outcome],
    )


WHERE_THE_REASON_IS = LocatorBundle(
    description="the application's stated reason",
    strategies=[FieldId(kind="field_id", id="loanRequestDenied")],
)


def engine_for(outcome: Outcome, screen: Snapshot, tmp_path: Path) -> Replay:
    return Replay(
        loan_capability(outcome),
        cast(BrowserSurface, OneScreen(screen)),
        Evidence(tmp_path, "r", frozenset()),
    )


def test_a_refusal_is_reported_in_the_applications_own_words(tmp_path: Path) -> None:
    """This application has four refusal wordings depending on which of funds and
    down payment fell short. Quoting whichever came back beats paraphrasing, and it
    is the difference between an answer a caller can act on and a shrug."""
    reason = "We cannot grant a loan in that amount with your available funds."
    engine = engine_for(refusal(WHERE_THE_REASON_IS), denied_screen(reason), tmp_path)

    answer = engine._business("LOAN_DENIED", "apply")

    assert answer.message == f"The bank declined the loan request. {reason}"


def test_a_second_wording_is_quoted_just_as_faithfully(tmp_path: Path) -> None:
    """Nothing may be hardcoded to one of the four."""
    reason = "You do not have sufficient funds for the given down payment."
    engine = engine_for(refusal(WHERE_THE_REASON_IS), denied_screen(reason), tmp_path)

    assert reason in engine._business("LOAN_DENIED", "apply").message


def test_an_outcome_with_nothing_to_quote_still_reports(tmp_path: Path) -> None:
    """Most outcomes have no reason on the page, and one raised by a step has no
    page at all. Absent is normal, not an error."""
    engine = engine_for(refusal(None), denied_screen("ignored"), tmp_path)

    assert engine._business("LOAN_DENIED", "apply").message == "The bank declined the loan request."


def test_a_reason_the_page_does_not_show_is_left_out(tmp_path: Path) -> None:
    """The locator is declared but resolves to nothing. Better a shorter message
    than a crash while reporting an answer."""
    bare = Snapshot(url="/x", title="x", anchors=[], controls=[])
    engine = engine_for(refusal(WHERE_THE_REASON_IS), bare, tmp_path)

    assert engine._business("LOAN_DENIED", "apply").message == "The bank declined the loan request."


# -- the audit trail of a handover ------------------------------------------


def test_the_log_counts_what_the_person_actually_did(tmp_path: Path) -> None:
    """The intervention is read from disk before the person's clicks are written into
    it, so its own copy still says zero. The log must not repeat that: an audit trail
    that undercounts a human is worse than one that never mentions them."""
    engine = engine_for(refusal(None), denied_screen("x"), tmp_path)
    stale = Intervention(
        id="r",
        run="r",
        capability="loan",
        step="apply",
        reason="held",
        status="handled",
        operator="pnh",
    )
    assert stale.human_actions == [], "the stale copy is the whole point"

    engine._control_returned(
        stale,
        NeedsHuman(capability="loan", version=1, step="apply", reason="held"),
        "r",
        tmp_path / "r.json",
        1,
    )

    logged = (tmp_path / "r" / "run.jsonl").read_text(encoding="utf-8")
    assert '"human_actions": 1' in logged


# -- asking a person about a failure, instead of only reporting it -----------


class FakePage:
    """Just enough page for a screenshot on failure."""

    def screenshot(self, path: str, **_: object) -> None:
        Path(path).write_bytes(b"")


class FakeSurface:
    def __init__(self) -> None:
        self.page = FakePage()


def stuck_engine(tmp_path: Path, *, store: bool, seconds: int) -> Replay:
    capability = Capability(
        id="signon",
        version=1,
        title="t",
        description="d",
        target=Target(app="parabank", entry="/parabank/index.htm"),
        steps=[
            Step(id="click_log_in", action="navigate"),
            Step(id="after_it", action="navigate"),
        ],
        success=TextVisible(kind="text_visible", text="Welcome"),
    )
    return Replay(
        capability,
        cast(BrowserSurface, FakeSurface()),
        Evidence(tmp_path / "evidence", "replay-test", frozenset()),
        interventions=InterventionStore(tmp_path / "inbox") if store else None,
        wait_seconds=seconds,
    )


BROKEN = Outcome(
    name="APP_ERROR",
    classification="hard_failure",
    message="The application showed its internal error page.",
    detect=TextVisible(kind="text_visible", text="An internal error has occurred"),
)


def test_a_failure_the_artifact_declared_does_not_go_to_a_person(tmp_path: Path) -> None:
    """The author already said this condition is terminal and what it means. Calling
    an operator to look at a refused entitlement wastes them, and the request would
    have no action attached to it."""
    engine = stuck_engine(tmp_path, store=True, seconds=300)
    answer = engine._stuck(
        engine.cap.steps[0], BROKEN, expected="the Log In button", observed="APP_ERROR: broken"
    )
    assert isinstance(answer, HardFailure)
    assert list((tmp_path / "inbox").glob("*.json")) == [], "nobody should have been asked"


def test_with_nobody_waiting_a_failure_is_reported_exactly_as_before(tmp_path: Path) -> None:
    """Every run without `--wait-for-human` has to behave the way it always did.
    Raising a request nobody is watching would turn an exit 1 into a hang."""
    engine = stuck_engine(tmp_path, store=True, seconds=0)
    answer = engine._stuck(
        engine.cap.steps[0], None, expected="the Log In button", observed="could not find it"
    )
    assert isinstance(answer, HardFailure)
    assert list((tmp_path / "inbox").glob("*.json")) == []


def test_the_skip_flag_is_spent_on_the_step_it_was_raised_for(tmp_path: Path) -> None:
    """The bug this catches is silent and one step wide.

    `_escalate` sets the skip flag for the step it was called about. At a risky step
    that flag is read at the top of the *next* call to `_step`, which is correct there
    because the step has not run yet. A failure is different: we are already inside the
    step, so an unspent flag skips whatever comes after it. A person fixes the sign-on
    and the run silently never fills the form.
    """
    engine = stuck_engine(tmp_path, store=True, seconds=300)

    def handled(*_: object, **__: object) -> None:
        engine._skip_next = True  # what `_escalate` does when the person says --handled

    engine._escalate = handled  # type: ignore[method-assign]
    answer = engine._stuck(
        engine.cap.steps[0], None, expected="the Log In button", observed="could not find it"
    )

    assert answer is None, "the run carries on from the step after this one"
    assert engine._skip_next is False, "the flag belongs to this step and must be spent here"
    assert [r.id for r in engine.records] == ["click_log_in"]
    assert engine.records[0].recovered_from == "performed by human"


def test_taking_control_without_finishing_the_step_is_still_a_failure(tmp_path: Path) -> None:
    """A failed step cannot be approved into working: the action already ran. If an
    operator hands control back without doing it, the honest answer is the failure we
    started with, not a step quietly skipped."""
    engine = stuck_engine(tmp_path, store=True, seconds=300)
    engine._escalate = lambda *_, **__: None  # type: ignore[assignment,method-assign]
    answer = engine._stuck(
        engine.cap.steps[0], None, expected="the Log In button", observed="could not find it"
    )
    assert isinstance(answer, HardFailure)
    assert "did not complete the step" in answer.observed
    assert engine.records == [], "nothing was performed, so nothing is recorded"


def test_a_failed_step_is_not_offered_for_approval() -> None:
    """`--approve` means "go ahead and do it", which answers a step that has not run.
    The action behind a failed step already ran. Offering the verb would invite a
    decision the engine has to refuse, so the banner does not print it."""
    from finautomate.replay import _waiting_banner

    request = Intervention(id="r1", run="r1", step="click_log_in", reason="x", kind="failed_step")
    risky = _waiting_banner("click_log_in", "x", request, Path("i.json"), 60, can_approve=True)
    failed = _waiting_banner("click_log_in", "x", request, Path("i.json"), 60, can_approve=False)

    assert "--approve" in risky
    assert "--approve" not in failed
    for verb in ("--handled", "--reject"):
        assert verb in failed, "a person still has to be able to answer"
