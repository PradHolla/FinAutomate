"""Running a recorded capability. No model, ever.

Nothing is retried unless the artifact declares it, and a risky step always stops
an unattended run.
"""

import re
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from pydantic import SecretStr

from finautomate.artifact import (
    Capability,
    Classification,
    LocatorBundle,
    Outcome,
    Recovery,
    Step,
)
from finautomate.checkpoint import satisfied, wait_for
from finautomate.evidence import Evidence
from finautomate.handover import start_watching
from finautomate.locate import Resolution, explain, resolve
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


def detected(
    outcomes: Sequence[Outcome], classification: Classification, snapshot: Snapshot
) -> Outcome | None:
    """The first declared outcome of this class whose detector holds on this screen.

    First-declared wins. The ordering is the artifact's decision, not this function's.
    """
    for declared in outcomes:
        if declared.classification != classification or declared.detect is None:
            continue
        if satisfied(declared.detect, snapshot):
            return declared
    return None


def with_runtime_outcomes(capability: Capability, declared: Sequence[Outcome]) -> Capability:
    """Add the target application's runtime conditions to a recorded capability.

    These come from tenant config, not the model: people and config decide risk.
    The capability keeps priority for any outcome name it already declares.
    """
    known = {o.name for o in capability.outcomes}
    extra = [o for o in declared if o.name not in known]
    if not extra:
        return capability
    return capability.model_copy(update={"outcomes": [*capability.outcomes, *extra]})


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

    The result may hold a password. It is never logged.
    """

    def swap(match: re.Match[str]) -> str:
        value = bound.get(match.group(1), match.group(0))
        return value.get_secret_value() if isinstance(value, SecretStr) else value

    return PARAM.sub(swap, template)


def _waiting_banner(
    step_id: str, reason: str, request: Intervention, path: Path, seconds: int
) -> str:
    rule = "=" * 74
    return (
        f"\n{rule}\nWAITING FOR A PERSON - held at {step_id}\n{rule}\n{reason}\n"
        f"\n  screenshot : {request.screenshot}"
        f"\n  request    : {path}"
        f"\n  waiting    : up to {seconds}s. The browser stays open.\n"
        "\nIn another terminal, choose one:\n"
        f"\n  uv run finautomate resolve {request.id} --approve   # let the automation do it"
        f"\n  uv run finautomate resolve {request.id} --handled   # you did it yourself"
        f"\n  uv run finautomate resolve {request.id} --reject    # do not proceed\n"
    )


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
        # The channel for a person; evidence.event() is the channel for the record.
        self._announce = announce or (lambda _message: None)
        self.records: list[StepRecord] = []
        self.outputs: dict[str, str] = {}
        self.recoveries: dict[str, int] = {}
        self._skip_next = False
        self.session_lost = False

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
                    if (stopped := self._recover(outcome)) is not None:
                        return self._finish(stopped, started)
                    # `dismiss` leaves the flow where it was, so retry this step.
                    index = 0 if outcome.action == "restart" else index
                    continue
                if outcome is not None:
                    return self._finish(outcome, started)
                index += 1
        except PlaywrightError as err:
            # Report a hard failure naming the step, not a raw traceback.
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

    def _step(self, step: Step, bound: dict[str, str | SecretStr]) -> Result | Recovery | None:
        """None to carry on, a Recovery to restart, or a Result to stop."""
        began = time.monotonic()

        if step.risk == "risky" and not self.attended:
            if (held := self._hold_for_person(step)) is not None:
                return held
            if self._done_by_person(step):
                return None

        if step.action == "navigate":
            self.surface.navigate(self.cap.target.entry)
            return None

        assert step.target is not None  # the schema guarantees this for these actions
        snapshot = self.surface.observe()
        found = resolve(step.target, snapshot)
        if found.control is None:
            return self._unresolved(step, snapshot, found)

        if (stopped := self._apply(step, found.control.ref, bound)) is not None:
            return stopped

        self._record(step, found, began)
        return self._verify(step)

    def _hold_for_person(self, step: Step) -> Result | None:
        """Stop an unattended run at an irreversible step. None once control is back."""
        reason = (
            f"{step.id} is marked irreversible and this run is unattended. "
            f"It would have: {step.target.description if step.target else step.action}."
        )
        self.evidence.event("risky_step_held", step=step.id, reason=reason)
        return self._escalate(step, reason)

    def _done_by_person(self, step: Step) -> bool:
        """Whether the operator performed this step themselves, so we skip it."""
        if not self._skip_next:
            return False
        self._skip_next = False
        self.records.append(
            StepRecord(id=step.id, action=step.action, recovered_from="performed by human")
        )
        return True

    def _unresolved(self, step: Step, snapshot: Snapshot, found: Resolution) -> Result | Recovery:
        assert step.target is not None
        if (answer := self._classify(step, snapshot)) is not None:
            return answer
        return self._fail(
            step.id,
            expected=step.target.description,
            observed=self._diagnose(snapshot, explain(step.target, found)),
        )

    def _apply(
        self, step: Step, ref: str, bound: dict[str, str | SecretStr]
    ) -> Result | Recovery | None:
        """Perform the action. A Result means stop; None means it worked."""
        assert step.target is not None
        try:
            self._act(step, ref, bound)
        except OptionNotFoundError as err:
            # A missing dropdown is a broken page. A dropdown that is present but
            # offers no such option is the application answering.
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
        except PlaywrightError as err:
            # The control is there and the action still could not be performed: most
            # often something is sitting over it. Classify before calling it a crash,
            # because an interstitial covering the page is a declared condition with a
            # declared fix, not a broken application.
            if (answer := self._classify(step, self.surface.observe())) is not None:
                return answer
            return self._fail(
                step.id,
                expected=f"to {step.action} {step.target.description}",
                observed=str(err).strip().splitlines()[0],
            )
        return None

    def _record(self, step: Step, found: Resolution, began: float) -> None:
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

    def _verify(self, step: Step) -> Result | Recovery | None:
        """Wait for the step's checkpoint, then ask what the application said.

        Checked either way: a checkpoint can be satisfied by the wrong screen.
        """
        if step.expect is None:
            return None

        settled = wait_for(self.surface, step.expect)
        after = settled if settled is not None else self.surface.observe()

        if (declared := detected(self.cap.outcomes, "business_outcome", after)) is not None:
            return self._business(declared.name, step.id)
        if settled is not None:
            return None

        if (jump := self._try_recover(step, after)) is not None:
            return jump
        return self._fail(
            step.id,
            expected=f"after this step: {self._describe(step.expect)}",
            observed=self._diagnose(after, "it never appeared before the timeout"),
        )

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

    def _escalate(self, step: Step, reason: str) -> Result | None:
        """Raise an intervention. A Result stops the run; None means control came back."""
        request = Intervention(
            id=self.evidence.run_id,
            run=self.evidence.run_id,
            capability=self.cap.id,
            step=step.id,
            reason=reason,
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
        self._announce(_waiting_banner(step.id, reason, request, path, self.wait_seconds))

        # The lease is now with the person. Record what they do so the run has no gap
        # in its audit trail.
        seen: list[dict[str, Any]] = []
        start_watching(self.surface.page, seen)
        decided = self._await_decision(request.id)
        recorded = self._log_human_actions(request.id, seen)
        self._archive(request.id)

        if decided is None:
            return self._nobody_answered(step, stopped, request.id, path)
        return self._control_returned(decided, stopped, request.id, path, recorded)

    def _archive(self, intervention_id: str) -> None:
        """Copy the settled request into this run's evidence.

        The operator's inbox is a working directory that gets cleared. The evidence
        directory is the audit record and has to stand on its own, so the request goes
        in it too - read back from disk, so the copy carries what the person did.
        """
        if self.interventions is None:
            return
        settled = self.interventions.read(intervention_id)
        (self.evidence.dir / "intervention.json").write_text(
            settled.model_dump_json(indent=2), encoding="utf-8"
        )

    def _log_human_actions(self, intervention_id: str, seen: list[dict[str, Any]]) -> int:
        """File what the person did, and report how much that was.

        The count is returned rather than read back off the request: the request was
        read from disk before these were written into it, so its own copy still says
        zero. An audit trail that undercounts what a human did is worse than one that
        does not mention them.
        """
        if not seen or self.interventions is None:
            return 0
        self.interventions.record_actions(intervention_id, seen)
        for action in seen:
            self.evidence.event(
                "human_action",
                did=action.get("kind", ""),
                target=action.get("target", ""),
                value=action.get("value", ""),
            )
        return len(seen)

    def _nobody_answered(
        self, step: Step, stopped: NeedsHuman, intervention_id: str, path: Path
    ) -> Result:
        if self.session_lost:
            self.evidence.event("session_closed_during_handover", intervention=intervention_id)
            return HardFailure(
                capability=self.cap.id,
                version=self.cap.version,
                steps=self.records,
                step=step.id,
                expected="the browser session to stay open for the operator",
                observed="the window was closed, so the session could not be handed back",
            )
        self.evidence.event("handover_timed_out", intervention=intervention_id)
        return stopped.model_copy(
            update={
                "reason": f"{stopped.reason} No operator responded within {self.wait_seconds}s.",
                "intervention": str(path),
            }
        )

    def _control_returned(
        self,
        decided: Intervention,
        stopped: NeedsHuman,
        intervention_id: str,
        path: Path,
        human_actions: int,
    ) -> Result | None:
        self._announce(f"  control returned by {decided.operator or 'operator'}: {decided.status}")
        self.evidence.event(
            "control_returned",
            intervention=intervention_id,
            decision=decided.status,
            operator=decided.operator,
            human_actions=human_actions,
        )
        if decided.status == "rejected":
            why = decided.note or "no reason given"
            return stopped.model_copy(
                update={
                    "reason": f"An operator declined this step: {why}",
                    "intervention": str(path),
                }
            )
        # `handled` means the person did the step themselves, so skip it.
        self._skip_next = decided.status == "handled"
        return None

    def _await_decision(self, intervention_id: str) -> Intervention | None:
        """Poll until a person decides, the session dies, or we run out of patience."""
        assert self.interventions is not None
        deadline = time.monotonic() + self.wait_seconds
        baseline = self.surface.observe()
        noticed = False
        while time.monotonic() < deadline:
            # If the operator closed the window there is nothing left to hand back.
            # `is_closed()` misses a quit process, so poke the session instead.
            if not self._session_alive():
                self.session_lost = True
                return None
            current = self.interventions.read(intervention_id)
            if not current.open:
                return current

            # The screen moving is not the same as the operator deciding, but nudge
            # them once we notice, so they know to say so.
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

    def _recover(self, recovery: Recovery) -> Result | None:
        """Carry out a declared recovery. A Result means it could not be done."""
        if recovery.action == "restart":
            # Back to the entry point, not to step zero: after a session dies, every
            # screen behind the login page is gone too.
            self.surface.navigate(self.cap.target.entry)
            return None

        assert recovery.target is not None  # the schema guarantees it for dismiss
        found = resolve(recovery.target, self.surface.observe())
        if found.control is None:
            return self._fail(
                "recovery",
                expected=recovery.target.description,
                observed=explain(recovery.target, found),
            )
        self.surface.click(found.control.ref)
        self.evidence.event("dismissed", target=recovery.target.description)
        return None

    def _classify(self, step: Step, snapshot: Snapshot) -> Result | Recovery | None:
        """What the screen says about why this step could not proceed.

        Checks a business outcome before a recoverable one: an answer beats a retry.
        """
        if (answer := detected(self.cap.outcomes, "business_outcome", snapshot)) is not None:
            return self._business(answer.name, step.id)
        return self._try_recover(step, snapshot)

    def _try_recover(self, step: Step, snapshot: Snapshot) -> Recovery | None:
        """Only conditions the capability declared, and only as often as it allows."""
        declared = detected(self.cap.outcomes, "recoverable", snapshot)
        if declared is None:
            return None
        assert declared.recovery is not None  # the schema rejects a recoverable without one
        used = self.recoveries.get(declared.name, 0)
        if used >= declared.recovery.max_attempts:
            self.evidence.event("recovery_exhausted", outcome=declared.name, attempts=used)
            return None
        self.recoveries[declared.name] = used + 1
        self.evidence.event("recovering", outcome=declared.name, at_step=step.id, attempt=used + 1)
        return declared.recovery

    def _diagnose(self, snapshot: Snapshot, fallback: str) -> str:
        """Let the artifact name a failure it declared how to spot, if it can.

        Names the failure as the application's, not ours.
        """
        declared = detected(self.cap.outcomes, "hard_failure", snapshot)
        if declared is None:
            return fallback
        self.evidence.event("detected", outcome=declared.name)
        return f"{declared.name}: {declared.message or 'the screen matched this condition'}"

    def _explained(self, where: LocatorBundle) -> str:
        """The application's own words about an outcome, if they are on screen.

        Missing is normal rather than an error: the same outcome can be raised by a
        step, where there is no page saying anything.
        """
        found = resolve(where, self.surface.observe())
        if found.control is None:
            return ""
        return self.surface.read(found.control.ref).strip()

    def _business(self, name: str, step_id: str) -> BusinessOutcome:
        declared = next((o for o in self.cap.outcomes if o.name == name), None)
        message = declared.message if declared and declared.message else name
        if declared and declared.explain and (why := self._explained(declared.explain)):
            message = f"{message} {why}"
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


def signature(capability: Capability) -> list[str]:
    """The capability's parameter list, on one line.

    The model names these itself, and it does not name them the same way twice, so a
    caller needs somewhere shorter than the YAML to read them off. Markers are only
    explained when one is actually used.
    """
    rendered: list[str] = []
    for spec in capability.inputs:
        marked = spec.name + ("*" if spec.secret else "") + ("" if spec.required else "?")
        # A constraint bind() already enforces, so printing it is not a promise
        # made twice.
        choices = "|".join(spec.values) if spec.values else ""
        rendered.append(f"{marked}={choices}" if choices else marked)

    lines = ["  takes: " + (", ".join(rendered) or "nothing")]
    if any(i.secret for i in capability.inputs):
        lines.append("         * never logged or written to disk")
    if any(not i.required for i in capability.inputs):
        lines.append("         ? optional")
    return lines


def dry_run(capability: Capability, supplied: dict[str, str]) -> list[str]:
    """What a replay would do, without opening a browser.

    Arguments are validated when there are any, so a bad one is caught here rather
    than halfway through a real run. With none, the caller is asking what this
    capability takes rather than claiming to have it, so the plan prints instead of
    a complaint about the arguments they did not give.
    """
    if supplied:
        bind(capability, supplied)
    lines = [f"{capability.id} v{capability.version}: {capability.title}"]
    lines += signature(capability)
    for step in capability.steps:
        target = step.target.description if step.target else capability.target.entry
        flag = "  [RISKY - needs a person]" if step.risk == "risky" else ""
        value = f" = {step.value}" if step.value else ""
        lines.append(f"  {step.id:<32} {step.action:<8} {target}{value}{flag}")
    lines.append(f"  success: {Replay._describe(capability.success)}")
    for output in capability.outputs:
        lines.append(f"  returns: {output.name} (from {output.from_step})")
    return lines
