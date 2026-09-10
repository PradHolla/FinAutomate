"""Running a recorded capability. No model, ever.

This is the production path: the thing an AI agent triggers when it wants a job
done. It reads the artifact, resolves each control the recorded way, and reports one
of four outcomes.

Two rules the engine holds to:

**Nothing is retried unless the artifact said it could be.** Recovery is declared
per capability, with a bounded attempt count. Generic retry-on-anything is how
automation quietly hammers a production system while looking like it is working.

**A risky step stops an unattended run.** The artifact marks the one action that
creates something real; running it without a person is not a decision this engine
gets to make.
"""

import re
import time
from collections.abc import Callable
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from pydantic import SecretStr

from finautomate.artifact import Capability, Recovery, Step
from finautomate.checkpoint import satisfied, wait_for
from finautomate.evidence import Evidence
from finautomate.handover import start_watching
from finautomate.locate import explain, resolve
from finautomate.result import (
    BusinessOutcome,
    HardFailure,
    NeedsHuman,
    Result,
    StepRecord,
    Success,
)
from finautomate.session import Intervention, InterventionStore
from finautomate.surface.browser import BrowserSurface, ControlNotFoundError, OptionNotFoundError
from finautomate.surface.models import Snapshot

PARAM = re.compile(r"\{\{\s*(\w+)\s*\}\}")


class ParameterError(ValueError):
    """The supplied arguments do not satisfy the capability's declared inputs.

    Raised before a browser is opened. Half-running a flow because an argument was
    missing leaves the application in a state nobody chose.
    """


def bind(capability: Capability, supplied: dict[str, str]) -> dict[str, str | SecretStr]:
    """Check arguments against the declared inputs and wrap the secret ones."""
    declared = {i.name: i for i in capability.inputs}
    if unknown := sorted(set(supplied) - set(declared)):
        raise ParameterError(f"{capability.id} takes no parameter named {unknown}")

    bound: dict[str, str | SecretStr] = {}
    missing: list[str] = []
    for name, spec in declared.items():
        if name not in supplied:
            if spec.required:
                missing.append(name)
            continue
        value = supplied[name]
        if spec.type == "enum" and spec.values and value not in spec.values:
            raise ParameterError(f"{name}={value!r} is not one of {spec.values}")
        bound[name] = SecretStr(value) if spec.secret else value
    if missing:
        raise ParameterError(f"{capability.id} requires {missing}")
    return bound


def render(template: str, bound: dict[str, str | SecretStr]) -> str:
    """Substitute `{{name}}`. Secrets are unwrapped here and nowhere else.

    The returned string may hold a password, so it goes straight to the browser. It
    is never logged: `Evidence` is constructed with the secret values and redacts
    them from anything written, so a mistake here is caught downstream rather than
    relied upon not to happen.
    """

    def swap(match: re.Match[str]) -> str:
        value = bound.get(match.group(1), match.group(0))
        return value.get_secret_value() if isinstance(value, SecretStr) else value

    return PARAM.sub(swap, template)


class Replay:
    def __init__(
        self,
        capability: Capability,
        surface: BrowserSurface,
        evidence: Evidence,
        *,
        attended: bool = False,
        interventions: InterventionStore | None = None,
        wait_seconds: int = 0,
        announce: Callable[[str], None] | None = None,
    ) -> None:
        self.cap = capability
        self.surface = surface
        self.evidence = evidence
        self.attended = attended
        self.interventions = interventions
        self.wait_seconds = wait_seconds
        # A run that pauses for a person has to say so. Everything below writes to
        # the evidence log for the record; this is the channel for the person.
        self._announce = announce or (lambda _message: None)
        self.records: list[StepRecord] = []
        self.outputs: dict[str, str] = {}
        self.recoveries: dict[str, int] = {}
        self._skip_next = False
        self.session_lost = False

    # -- the run ------------------------------------------------------------

    def run(self, supplied: dict[str, str]) -> Result:
        bound = bind(self.cap, supplied)
        started = time.monotonic()
        self.evidence.event(
            "replay_started",
            capability=self.cap.id,
            version=self.cap.version,
            attended=self.attended,
            parameters=sorted(bound),
        )
        try:
            self.surface.navigate(self.cap.target.entry)

            index = 0
            while index < len(self.cap.steps):
                step = self.cap.steps[index]
                outcome = self._step(step, bound)
                if isinstance(outcome, Recovery):
                    index = self._index_of(outcome.step)
                    continue
                if outcome is not None:
                    return self._finish(outcome, started)
                index += 1
        except PlaywrightError as err:
            # The application is unreachable, or the browser gave up. A caller gets
            # a hard failure naming the step, not a traceback from three layers down.
            step_id = self.records[-1].id if self.records else "navigate"
            return self._finish(
                self._fail(
                    step_id,
                    expected="the application to respond",
                    observed=str(err).strip().splitlines()[0],
                ),
                started,
            )

        if wait_for(self.surface, self.cap.success) is None:
            return self._finish(
                self._fail(
                    "success",
                    expected=f"the success condition: {self._describe(self.cap.success)}",
                    observed="it never held before the timeout",
                ),
                started,
            )
        return self._finish(
            Success(capability=self.cap.id, version=self.cap.version, outputs=self.outputs),
            started,
        )

    # -- one step -----------------------------------------------------------

    def _step(self, step: Step, bound: dict[str, str | SecretStr]) -> Result | Recovery | None:
        """None to carry on, a Recovery to jump, or a Result to stop."""
        began = time.monotonic()

        if step.risk == "risky" and not self.attended:
            reason = (
                f"{step.id} is marked irreversible and this run is unattended. "
                f"It would have: {step.target.description if step.target else step.action}."
            )
            self.evidence.event("risky_step_held", step=step.id, reason=reason)
            held = self._escalate(step, reason, kind="risky_step")
            if held is not None:
                return held
            # A person approved it, or did it themselves. Either way the lease is
            # back with us, so fall through and carry on.
            if self._skip_next:
                self._skip_next = False
                self.records.append(
                    StepRecord(id=step.id, action=step.action, recovered_from="performed by human")
                )
                return None

        if step.action == "navigate":
            self.surface.navigate(self.cap.target.entry)
            return None

        assert step.target is not None  # the schema guarantees this for these actions
        snapshot = self.surface.observe()
        found = resolve(step.target, snapshot)

        if found.control is None:
            if (jump := self._try_recover(step, snapshot)) is not None:
                return jump
            return self._fail(
                step.id,
                expected=step.target.description,
                observed=explain(step.target, found),
            )

        try:
            self._act(step, found.control.ref, bound)
        except OptionNotFoundError as err:
            # The control was found; the application simply is not offering this
            # choice. That distinction is the whole point - a missing dropdown is a
            # broken page, a missing option is the application answering.
            if name := step.outcomes.get("option_not_found"):
                return self._business(name, step.id)
            return self._fail(
                step.id,
                expected=f"an option {err.label!r}",
                observed=f"available options were {err.available_labels[:12]}",
            )
        except ControlNotFoundError:
            return self._fail(
                step.id, expected=step.target.description, observed="it vanished before the action"
            )

        self.records.append(
            StepRecord(
                id=step.id,
                action=step.action,
                strategy_index=found.strategy_index,
                strategy_kind=found.strategy_kind,
                used_fallback=found.used_fallback,
                duration_ms=int((time.monotonic() - began) * 1000),
            )
        )
        self.evidence.event(
            "step",
            id=step.id,
            action=step.action,
            strategy=found.strategy_kind,
            tier=found.strategy_index,
            fallback=found.used_fallback,
        )

        if step.expect is not None and wait_for(self.surface, step.expect) is None:
            return self._fail(
                step.id,
                expected=f"after this step: {self._describe(step.expect)}",
                observed="it never appeared before the timeout",
            )
        return None

    def _act(self, step: Step, ref: str, bound: dict[str, str | SecretStr]) -> None:
        if step.action == "click":
            self.surface.click(ref)
        elif step.action == "type":
            self.surface.type(ref, render(step.value or "", bound))
        elif step.action == "select":
            self.surface.select(ref, render(step.value or "", bound))
        elif step.action == "read":
            value = self.surface.read(ref)
            for output in self.cap.outputs:
                if output.from_step == step.id:
                    self.outputs[output.name] = value

    # -- handing over to a person -------------------------------------------

    def _escalate(self, step: Step, reason: str, *, kind: str) -> Result | None:
        """Raise an intervention. Returns a Result to stop, or None to carry on.

        With no store configured, or no time to wait, this is what the engine did
        before: report that a person is needed and exit. Given both, it becomes a
        real handover - the browser stays open on the same page, the lease passes to
        a person, what they do is recorded, and control comes back.
        """
        request = Intervention(
            id=self.evidence.run_id,
            run=self.evidence.run_id,
            capability=self.cap.id,
            step=step.id,
            reason=reason,
            kind=kind,  # type: ignore[arg-type]
            screenshot=str(self.evidence.screenshot(self.surface.page, f"held-{step.id}")),
            screen=[f"{c.ref} {c.role} {c.name!r}" for c in self.surface.observe().controls[:40]],
        )
        stopped = NeedsHuman(
            capability=self.cap.id,
            version=self.cap.version,
            steps=self.records,
            step=step.id,
            reason=reason,
        )
        if self.interventions is None or self.wait_seconds <= 0:
            return stopped

        path = self.interventions.write(request)
        self.evidence.event(
            "handed_to_human", intervention=request.id, step=step.id, file=str(path)
        )
        self._announce(
            "\n"
            + "=" * 74
            + f"\nWAITING FOR A PERSON - held at {step.id}\n"
            + "=" * 74
            + f"\n{reason}\n"
            f"\n  screenshot : {request.screenshot}"
            f"\n  request    : {path}"
            f"\n  waiting    : up to {self.wait_seconds}s. The browser stays open.\n"
            "\nIn another terminal, choose one:\n"
            f"\n  uv run finautomate resolve {request.id} --approve   # let the automation do it"
            f"\n  uv run finautomate resolve {request.id} --handled   # you did it yourself"
            f"\n  uv run finautomate resolve {request.id} --reject    # do not proceed\n"
        )

        # The lease is now held by a person and the automation does nothing but
        # watch. It is watching in both senses: waiting for the decision, and
        # recording what they do so the run has no gap in its audit trail.
        seen: list[dict[str, Any]] = []
        start_watching(self.surface.page, seen)
        decided = self._await_decision(request.id)

        if seen:
            self.interventions.record_actions(request.id, seen)
            for action in seen:
                self.evidence.event(
                    "human_action",
                    did=action.get("kind", ""),
                    target=action.get("target", ""),
                    value=action.get("value", ""),
                )

        if decided is None:
            if self.session_lost:
                self.evidence.event("session_closed_during_handover", intervention=request.id)
                return HardFailure(
                    capability=self.cap.id,
                    version=self.cap.version,
                    steps=self.records,
                    step=step.id,
                    expected="the browser session to stay open for the operator",
                    observed="the window was closed, so the session could not be handed back",
                )
            self.evidence.event("handover_timed_out", intervention=request.id)
            return stopped.model_copy(
                update={
                    "reason": f"{reason} No operator responded within {self.wait_seconds}s.",
                    "intervention": str(path),
                }
            )

        self._announce(f"  control returned by {decided.operator or 'operator'}: {decided.status}")
        self.evidence.event(
            "control_returned",
            intervention=request.id,
            decision=decided.status,
            operator=decided.operator,
            human_actions=len(decided.human_actions),
        )
        if decided.status == "rejected":
            declined = decided.note or "no reason given"
            return stopped.model_copy(
                update={
                    "reason": f"An operator declined this step: {declined}",
                    "intervention": str(path),
                }
            )
        self._skip_next = decided.status == "handled"
        return None

    def _await_decision(self, intervention_id: str) -> Intervention | None:
        """Poll until a person decides, the session dies, or we run out of patience."""
        assert self.interventions is not None
        deadline = time.monotonic() + self.wait_seconds
        baseline = self.surface.observe()
        noticed = False
        while time.monotonic() < deadline:
            # The offer being made is control of *this* session. If the operator
            # closes the window, there is nothing left to hand back and no reason to
            # keep waiting - so say so immediately rather than sitting out the whole
            # timeout and failing later for a reason that looks unrelated.
            #
            # `is_closed()` alone is not enough: it reports a page closed through the
            # protocol, and a window the operator quits kills the process instead, so
            # the flag never gets set. Poking the session is what actually tells us.
            if not self._session_alive():
                self.session_lost = True
                return None
            current = self.interventions.read(intervention_id)
            if not current.open:
                return current

            # Noticing the screen has moved is not the same as deciding the step is
            # done. Only a named operator gets to decide that, because the record of
            # who approved an irreversible action is the point. But saying nothing
            # while someone works in the browser, waiting for a signal we never
            # asked for, is how this feature goes unused.
            if not noticed and self._screen_moved(baseline):
                noticed = True
                self._announce(
                    f"  ...the screen has changed. If you completed the step, run:\n"
                    f"     uv run finautomate resolve {intervention_id} --handled"
                )
            time.sleep(1.0)
        return None

    def _screen_moved(self, baseline: Snapshot) -> bool:
        try:
            now = self.surface.observe()
        except PlaywrightError:
            return False
        return now.url != baseline.url or len(now.controls) != len(baseline.controls)

    def _session_alive(self) -> bool:
        if self.surface.page.is_closed():
            return False
        try:
            self.surface.page.title()
        except PlaywrightError:
            return False
        return True

    # -- recovery -----------------------------------------------------------

    def _try_recover(self, step: Step, snapshot: Snapshot) -> Recovery | None:
        """Only conditions the capability declared, and only as often as it allows."""
        for declared in self.cap.outcomes:
            if declared.classification != "recoverable" or declared.recovery is None:
                continue
            if declared.detect is None or not satisfied(declared.detect, snapshot):
                continue
            used = self.recoveries.get(declared.name, 0)
            if used >= declared.recovery.max_attempts:
                self.evidence.event("recovery_exhausted", outcome=declared.name, attempts=used)
                return None
            self.recoveries[declared.name] = used + 1
            self.evidence.event(
                "recovering", outcome=declared.name, at_step=step.id, attempt=used + 1
            )
            return declared.recovery
        return None

    def _index_of(self, step_id: str) -> int:
        for index, step in enumerate(self.cap.steps):
            if step.id == step_id:
                return index
        raise ParameterError(f"recovery points at unknown step {step_id!r}")

    # -- results ------------------------------------------------------------

    def _business(self, name: str, step_id: str) -> BusinessOutcome:
        declared = next((o for o in self.cap.outcomes if o.name == name), None)
        message = declared.message if declared and declared.message else name
        self.evidence.event("business_outcome", outcome=name, step=step_id, message=message)
        return BusinessOutcome(
            capability=self.cap.id,
            version=self.cap.version,
            steps=self.records,
            outcome=name,
            message=message,
            step=step_id,
        )

    def _fail(self, step_id: str, *, expected: str, observed: str) -> HardFailure:
        shot = self.evidence.screenshot(self.surface.page, f"failed-{step_id}")
        self.evidence.event(
            "hard_failure", step=step_id, expected=expected, observed=observed, screenshot=str(shot)
        )
        return HardFailure(
            capability=self.cap.id,
            version=self.cap.version,
            steps=self.records,
            step=step_id,
            expected=expected,
            observed=observed,
            screenshot=str(shot),
        )

    def _finish(self, result: Result, started: float) -> Result:
        final: Any = result.model_copy(
            update={
                "duration_ms": int((time.monotonic() - started) * 1000),
                "steps": self.records,
                "evidence": str(self.evidence.dir),
            }
        )
        self.evidence.event("replay_finished", outcome=final.kind, duration_ms=final.duration_ms)
        return final  # type: ignore[no-any-return]

    @staticmethod
    def _describe(checkpoint: Any) -> str:
        if getattr(checkpoint, "kind", "") == "text_visible":
            return f"the text {checkpoint.text!r}"
        return getattr(checkpoint.target, "description", "the expected control")


def dry_run(capability: Capability, supplied: dict[str, str]) -> list[str]:
    """What a replay would do, without opening a browser.

    Parameters are still validated, so a bad argument is caught here rather than
    halfway through a real run.
    """
    bind(capability, supplied)
    lines = [f"{capability.id} v{capability.version}: {capability.title}"]
    for step in capability.steps:
        target = step.target.description if step.target else capability.target.entry
        flag = "  [RISKY - needs a person]" if step.risk == "risky" else ""
        value = f" = {step.value}" if step.value else ""
        lines.append(f"  {step.id:<32} {step.action:<8} {target}{value}{flag}")
    lines.append(f"  success: {Replay._describe(capability.success)}")
    for output in capability.outputs:
        lines.append(f"  returns: {output.name} (from {output.from_step})")
    return lines
