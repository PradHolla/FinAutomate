"""The discovery run: a model drives the real UI once, and we write down what worked.

This is a while loop, not a framework. Observe the screen, ask the model for one
action, check it against policy, record how to find that control again, do it, look
again. About a hundred lines of that is the whole "agent".

Three decisions worth pointing at:

**The model never sees a screenshot.** It gets a text list of controls with roles
and names - the same view the replay engine has. If it chose targets by looking at
pixels it would answer in pixels, and pixel coordinates are the least durable thing
you can write into a recording.

**The model never sees a secret.** It cannot type a password even if it wants to.
It calls `type_secret` naming a declared parameter, and this module substitutes the
value on the way to the browser.

**Recording happens at the moment of each action**, against the snapshot the model
was looking at. A `ref` means nothing once the page changes, so reconstructing the
flow afterwards from a transcript would produce a list of dead handles.
"""

import re
import time
from datetime import UTC, datetime
from typing import Any

from anthropic import Anthropic
from anthropic.types import MessageParam, ToolParam, ToolUseBlock
from playwright.sync_api import Error as PlaywrightError

from finautomate.artifact import (
    Capability,
    Checkpoint,
    Input,
    LocatorBundle,
    Output,
    Recorded,
    Risk,
    Step,
    Target,
    TextVisible,
)
from finautomate.evidence import Evidence
from finautomate.policy import Guards, Policy
from finautomate.record import build_bundle
from finautomate.surface.browser import BrowserSurface, ControlNotFoundError, OptionNotFoundError
from finautomate.surface.models import Control, Snapshot

MODEL = "claude-sonnet-5"
SETTLE_SECONDS = 6.0
MAX_DONE_REJECTIONS = 2
"""How many times the loop will push back before giving up.

Pushing back is how a skipped parameter or an uncaptured output gets fixed. But if
the model cannot satisfy the contract - a field is not on the page at all - an
uncapped loop would refuse `done` until max_steps, paying for every turn. After
this many refusals the run ends with no artifact, which is the honest outcome: an
artifact missing a declared parameter is worse than none, because it validates."""

# The model's tool names and the artifact's action vocabulary are deliberately not
# the same. `type_secret` exists so the model can fill a password field without ever
# being given the value, but both it and `type_text` record as a plain `type` step.
# Policy is written in artifact actions, so every tool has to be mapped before the
# check - getting this wrong once already cost a run.
TOOL_ACTION = {
    "click": "click",
    "type_text": "type",
    "type_secret": "type",
    "select": "select",
    "read": "read",
    "navigate": "navigate",
}

SYSTEM = """You operate a legacy banking web application by choosing one action at a time.

You are shown a text list of what is on screen. Each line is either visible text or
a control you can act on:

    text  "Customer Login"
    c8    role=textbox name="" field=username

An empty name is normal in this application - most input fields have no label. Use
the text immediately above a field to work out what it is for.

Rules:
- One action per turn. After each one you will see the screen again.
- Refer to controls by their ref (c8). Refs change every turn; always use the ones
  from the list you were just shown.
- Give every action a short plain-English `description` of the control, like "the
  Username field". It is stored in the recording and shown to a human operator if
  the run ever needs help.
- For a parameter marked SECRET, use `type_secret` and name the parameter. You will
  never be given its value and do not need it.
- Set every parameter you were given explicitly, even when a field already appears
  to hold the right value. A default that happens to be correct today will not be
  correct for the next caller.
- If the goal asks for a value back, you must `read` it off the screen before
  calling `done`. A summary mentioning the value is not the same as capturing it.
- Call `done` when the goal is visibly achieved. For `success_text`, give a SHORT
  phrase that will read the same on every future run - "Account Opened" is right.
  Never include an account number, amount, date, or anything else specific to this
  run: that text becomes the permanent success check for every future caller.
- Call `stuck` rather than guessing if you cannot make progress."""


def _tools() -> list[ToolParam]:
    def control(extra: dict[str, Any] | None = None, *, required: list[str]) -> dict[str, Any]:
        props: dict[str, Any] = {
            "ref": {"type": "string", "description": "Control ref from the current screen."},
            "description": {
                "type": "string",
                "description": "Short plain-English name for this control.",
            },
        }
        props.update(extra or {})
        return {
            "type": "object",
            "properties": props,
            "required": ["ref", "description", *required],
            "additionalProperties": False,
        }

    return [
        {"name": "click", "description": "Click a control.", "input_schema": control(required=[])},
        {
            "name": "type_text",
            "description": "Type a value into a field.",
            "input_schema": control({"text": {"type": "string"}}, required=["text"]),
        },
        {
            "name": "type_secret",
            "description": "Type a secret parameter into a field without seeing its value.",
            "input_schema": control(
                {"parameter": {"type": "string", "description": "Name of the SECRET parameter."}},
                required=["parameter"],
            ),
        },
        {
            "name": "select",
            "description": "Choose an option in a dropdown by its visible label.",
            "input_schema": control({"label": {"type": "string"}}, required=["label"]),
        },
        {
            "name": "read",
            "description": "Read a value off the screen and return it to the caller.",
            "input_schema": control(
                {"output_name": {"type": "string", "description": "Name for this returned value."}},
                required=["output_name"],
            ),
        },
        {
            "name": "navigate",
            "description": "Go to a path within the application.",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
        {
            "name": "done",
            "description": "The goal is achieved.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "success_text": {
                        "type": "string",
                        "description": "Text visible on screen that proves success.",
                    },
                },
                "required": ["summary", "success_text"],
                "additionalProperties": False,
            },
        },
        {
            "name": "stuck",
            "description": "Cannot make progress. Hand off to a person.",
            "input_schema": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
                "additionalProperties": False,
            },
        },
    ]


def render(snapshot: Snapshot) -> str:
    """The screen as the model sees it. Text only - no image, ever."""
    rows: list[tuple[int, str]] = [
        (a.doc_order, f'  text  "{a.text[:90]}"') for a in snapshot.anchors
    ]
    for c in snapshot.controls:
        bits = [f"role={c.role}", f'name="{c.name}"']
        if c.field_name:
            bits.append(f"field={c.field_name}")
        if c.value:
            bits.append(f'value="{c.value[:40]}"')
        if c.options:
            bits.append(f"options={c.options[:12]}")
        if not c.enabled:
            bits.append("DISABLED")
        rows.append((c.doc_order, f"  {c.ref:<5} {' '.join(bits)}"))
    body = "\n".join(text for _, text in sorted(rows))
    return f"Screen: {snapshot.title}\n\n{body}"


def _stable_anchor(candidates: list[str]) -> str | None:
    """The shortest newly-appeared text that will read the same on every run.

    Anything containing a digit is rejected: account numbers, amounts and dates all
    change between runs, and a checkpoint that only matches one run is a checkpoint
    that fails on every other one.
    """
    usable = [
        text for text in candidates if 4 <= len(text) <= 60 and not any(ch.isdigit() for ch in text)
    ]
    return min(usable, key=len) if usable else None


def _slug(text: str, action: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", "", text.casefold()).split()
    drop = {"the", "a", "an", "button", "field", "link", "dropdown", "on", "of", "to"}
    core = "_".join(w for w in words if w not in drop)[:40] or action
    return f"{action}_{core}".strip("_")


class Discovery:
    def __init__(
        self,
        *,
        surface: BrowserSurface,
        client: Anthropic,
        policy: Policy,
        guards: Guards,
        evidence: Evidence,
        params: dict[str, str],
        secrets: dict[str, str],
        expect_outputs: tuple[str, ...] = (),
    ) -> None:
        self.surface = surface
        self.client = client
        self.policy = policy
        self.guards = guards
        self.evidence = evidence
        self.params = params
        self.secrets = secrets
        self.expect_outputs = expect_outputs
        self.steps: list[Step] = []
        self.outputs: list[Output] = []
        self.used_ids: set[str] = set()
        self.tokens_in = 0
        self.tokens_out = 0
        self.calls = 0
        self.success_text = ""
        self.summary = ""
        self.read_values: list[str] = []
        self.rejections = 0

    # -- parameterization ---------------------------------------------------

    def _as_reference(self, literal: str) -> str:
        """Replace a typed value with its parameter placeholder.

        The model types real values because it needs them to reason - it has to see
        that account 12345 is in the dropdown. The recording must not contain them,
        or the capability would only ever work for one customer. Any value that came
        from a parameter is swapped back on the way into the artifact.
        """
        for name, value in self.params.items():
            if value and literal == value:
                return f"{{{{{name}}}}}"
        return literal

    # -- the loop -----------------------------------------------------------

    def run(self, goal: str, entry: str, app: str) -> Capability | None:
        self.surface.navigate(entry)
        messages: list[MessageParam] = [{"role": "user", "content": self._opening(goal)}]
        started = time.monotonic()
        outcome = "max_steps"

        for step_number in range(1, self.guards.max_steps + 1):
            if time.monotonic() - started > self.guards.max_seconds:
                outcome = "timeout"
                break
            if self.tokens_in + self.tokens_out > self.guards.max_tokens_total:
                outcome = "token_budget"
                break

            snapshot = self.surface.observe()
            messages.append({"role": "user", "content": render(snapshot)})
            blocks = self._ask(messages)
            if not blocks:
                outcome = "model_stopped_without_acting"
                break
            block = blocks[0]

            self.evidence.event(
                "model_action",
                step=step_number,
                tool=block.name,
                input=block.input,
                url=snapshot.url,
            )

            unaccounted = self._missing_outputs() + self._missing_params()
            if block.name == "done" and unaccounted and self.rejections >= MAX_DONE_REJECTIONS:
                outcome = "incomplete"
                self.evidence.event("gave_up", missing=list(unaccounted))
                break

            if block.name == "done" and (missing := unaccounted):
                self.rejections += 1
                # The caller declared what this capability must return. Finishing
                # without it produces an artifact that satisfies its goal in prose
                # and returns nothing, which is worse than a failed run because it
                # looks fine. Observed: the model captured the account number on one
                # run and skipped it on the next, from the same prompt.
                self.evidence.event("done_rejected", step=step_number, missing=list(missing))
                wanted = ", ".join(missing)
                nudge = (
                    f"Not finished. These are still unaccounted for: {wanted}. "
                    "Every parameter must be set explicitly on the screen, even when a "
                    "field already looks correct, and every required output must be "
                    "captured with `read`. Do that, then call done."
                )
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "tool_result", "tool_use_id": block.id, "content": nudge}
                        ],
                    }
                )
                continue

            if block.name in ("done", "stuck"):
                outcome = block.name
                self._finish(block)
                break

            # Every tool_use must be answered or the next request is rejected.
            # Parallel calls are disabled, so extras should never arrive - but if
            # one does, an unanswered block ends the run with a 400.
            results: list[Any] = [
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": self._perform(block, snapshot, step_number),
                },
                *(
                    {
                        "type": "tool_result",
                        "tool_use_id": extra.id,
                        "content": "Not run. One action per turn - look at the screen again.",
                    }
                    for extra in blocks[1:]
                ),
            ]
            messages.append({"role": "user", "content": results})
            self._record_wait(snapshot)

        self.evidence.event(
            "run_finished",
            outcome=outcome,
            steps_recorded=len(self.steps),
            model_calls=self.calls,
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
        )
        self.evidence.transcript(messages)

        if outcome != "done" or not self.steps:
            return None
        return self._capability(goal, entry, app)

    def _missing_outputs(self) -> tuple[str, ...]:
        captured = {o.name for o in self.outputs}
        return tuple(name for name in self.expect_outputs if name not in captured)

    def _missing_params(self) -> tuple[str, ...]:
        """Parameters the caller supplied that no recorded step actually uses.

        An unused parameter is a lie in the capability's contract: the file claims
        to take a funding account and then ignores it. It happens when a field
        already holds an acceptable value and the model sees no reason to touch it -
        which is true for this run and false for the next caller. Observed twice on
        the same prompt, so the instruction is not enough on its own.
        """
        used = {p for step in self.steps for p in re.findall(r"\{\{(\w+)\}\}", step.value or "")}
        declared = {*self.params, *self.secrets}
        return tuple(sorted(declared - used))

    def _stale(self, expected: Control) -> str:
        """Empty string when the ref still points at the same control."""
        try:
            current = self.surface.observe().control(expected.ref)
        except KeyError:
            return f"Control {expected.ref} is no longer on the page."
        if current.role != expected.role or current.name != expected.name:
            return (
                f"Control {expected.ref} is now a {current.role} named "
                f"{current.name!r}, not a {expected.role} named {expected.name!r}."
            )
        return ""

    def _record_wait(self, before: Snapshot) -> None:
        """Wait for the page to respond, and write down what we waited for.

        Two jobs in one place, because they are the same observation.

        The wait: this application answers a submit by swapping one panel for
        another after the network has already gone quiet, so an immediate snapshot
        shows the form still standing. Without this the model concludes its click
        did nothing and clicks again - which, on the button that opens a bank
        account, is not a harmless mistake.

        The recording: replay has to make the same wait, and it has no model to
        notice the page is still catching up. So whatever text appeared as a result
        of this action becomes the step's checkpoint. Skipping this is what made an
        earlier recording read the account number off a form that had not been
        replaced yet.
        """
        deadline = time.monotonic() + SETTLE_SECONDS
        baseline = render(before)
        seen = {a.text for a in before.anchors}
        while time.monotonic() < deadline:
            after = self.surface.observe()
            if render(after) != baseline:
                self._attach_checkpoint(after, seen)
                return
            time.sleep(0.2)

    def _attach_checkpoint(self, after: Snapshot, seen: set[str]) -> None:
        """Give the step we just recorded a checkpoint made from newly arrived text."""
        if not self.steps:
            return
        fresh = [a.text for a in after.anchors if a.text not in seen]
        stable = _stable_anchor(fresh)
        if stable is None:
            return
        last = self.steps[-1]
        if last.expect is not None:
            return
        checkpoint: Checkpoint = TextVisible(kind="text_visible", text=stable, match="contains")
        self.steps[-1] = last.model_copy(update={"expect": checkpoint})
        self.evidence.event("recorded_checkpoint", step=last.id, wait_for=stable)

    def _opening(self, goal: str) -> str:
        lines = [f"Goal: {goal}"]
        if self.expect_outputs:
            lines += ["", f"This capability must return: {', '.join(self.expect_outputs)}."]
        lines += ["", "Parameters you may need:"]
        for name, value in self.params.items():
            lines.append(f"  {name} = {value!r}")
        for name in self.secrets:
            lines.append(f"  {name} = SECRET (use type_secret, you will not be shown it)")
        return "\n".join(lines)

    def _ask(self, messages: list[MessageParam]) -> list[ToolUseBlock]:
        response = self.client.messages.create(
            model=MODEL,
            max_tokens=4096,
            # The system prompt and tool definitions are identical on every turn and
            # get resent each time. Caching them is the single cheapest win here.
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            tools=_tools(),
            # One action per turn. The loop observes the screen between actions, so
            # a second action chosen from a stale screen would be acting blind.
            tool_choice={"type": "auto", "disable_parallel_tool_use": True},
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            messages=messages,
        )
        self.calls += 1
        self.tokens_in += response.usage.input_tokens
        self.tokens_out += response.usage.output_tokens
        messages.append({"role": "assistant", "content": response.content})
        return [b for b in response.content if isinstance(b, ToolUseBlock)]

    # -- one action ---------------------------------------------------------

    def _perform(self, block: ToolUseBlock, snapshot: Snapshot, step_number: int) -> str:
        args: dict[str, Any] = dict(block.input)

        if block.name == "navigate":
            path = str(args.get("path", ""))
            decision = self.policy.check_path(path)
            self.evidence.event("policy", step=step_number, target=path, verdict=decision.verdict)
            if decision.blocked:
                return f"REFUSED by policy: {decision.reason}"
            self.surface.navigate(path)
            self._add_step(Step(id=self._step_id("navigate", path), action="navigate"))
            return "navigated"

        ref = str(args.get("ref", ""))
        description = str(args.get("description", ref))
        try:
            control = snapshot.control(ref)
        except KeyError:
            return f"No control {ref!r} on the screen you were shown. Look again."

        # Refuse to act on a stale handle. The model chose this ref from a snapshot
        # taken before its turn; if the page has moved since, that ref may now point
        # at something else entirely. Rejecting is the whole reason these are
        # dedicated tools rather than a generic "run this" - the harness can enforce
        # an invariant the model cannot see.
        stale = self._stale(control)
        if stale:
            self.evidence.event("stale_ref", step=step_number, ref=ref, detail=stale)
            return f"{stale} The screen has changed since you looked. Look again."

        decision = self.policy.decide(
            TOOL_ACTION[block.name], control.name or control.text, control.role
        )
        self.evidence.event(
            "policy",
            step=step_number,
            tool=block.name,
            control=description,
            verdict=decision.verdict,
            reason=decision.reason,
        )
        if decision.blocked:
            return f"REFUSED by policy: {decision.reason}"

        # A `read` step is the one place where the control's own name is the data
        # we are about to return. Peek at it first so it can be kept out of the
        # locator - "the link named 13566" works exactly once.
        peeked = self.surface.read(control.ref) if block.name == "read" else ""
        forbid = frozenset(
            v for v in [*self.params.values(), *self.read_values, peeked] if v and len(v) > 2
        )
        bundle = build_bundle(control, snapshot, description, forbid)
        if bundle is None:
            return f"{description!r} cannot be recorded - no stable way to find it again."

        risk: Risk = "risky" if decision.verdict == "risky" else "safe"
        try:
            return self._act(block, args, control.ref, description, bundle, risk)
        except OptionNotFoundError as err:
            return f"No option {err.label!r}. Available: {err.available_labels[:12]}"
        except ControlNotFoundError:
            return "That control is no longer on the page. Look again."
        except PlaywrightError as err:
            # A failed action is information, not a fatal error. The page may have
            # moved under us between the snapshot and the click. Hand the reason back
            # and let the model look again rather than killing a run mid-flight.
            self.evidence.event(
                "action_failed", step=step_number, control=description, error=str(err)[:200]
            )
            first_line = str(err).strip().splitlines()[0]
            return f"That action failed: {first_line}. The screen may have changed - look again."

    def _act(
        self,
        block: ToolUseBlock,
        args: dict[str, Any],
        ref: str,
        description: str,
        bundle: LocatorBundle,
        risk: Risk,
    ) -> str:
        step_id = self._step_id(block.name, description)

        if block.name == "click":
            self.surface.click(ref)
            self._add_step(Step(id=step_id, action="click", target=bundle, risk=risk))
            return "clicked"

        if block.name == "type_text":
            literal = str(args.get("text", ""))
            self.surface.type(ref, literal)
            self._add_step(
                Step(
                    id=step_id,
                    action="type",
                    target=bundle,
                    value=self._as_reference(literal),
                    risk=risk,
                )
            )
            return "typed"

        if block.name == "type_secret":
            name = str(args.get("parameter", ""))
            if name not in self.secrets:
                return f"{name!r} is not a declared secret parameter."
            self.surface.type(ref, self.secrets[name])
            self._add_step(
                Step(id=step_id, action="type", target=bundle, value=f"{{{{{name}}}}}", risk=risk)
            )
            return "typed secret"

        if block.name == "select":
            label = str(args.get("label", ""))
            self.surface.select(ref, label)
            self._add_step(
                Step(
                    id=step_id,
                    action="select",
                    target=bundle,
                    value=self._as_reference(label),
                    by="label",
                    risk=risk,
                )
            )
            return f"selected {label!r}"

        if block.name == "read":
            value = self.surface.read(ref)
            self.read_values.append(value)
            output_name = str(args.get("output_name", "value"))
            self._add_step(Step(id=step_id, action="read", target=bundle, risk="safe"))
            self.outputs.append(Output(name=output_name, from_step=step_id))
            return f"read {value!r}"

        return f"Unknown tool {block.name!r}"

    # -- assembling the artifact -------------------------------------------

    def _step_id(self, action: str, description: str) -> str:
        base = _slug(description, action)
        candidate, n = base, 2
        while candidate in self.used_ids:
            candidate, n = f"{base}_{n}", n + 1
        return candidate

    def _add_step(self, step: Step) -> None:
        self.used_ids.add(step.id)
        self.steps.append(step)
        self.evidence.event("recorded_step", id=step.id, action=step.action, risk=step.risk)

    def _finish(self, block: ToolUseBlock) -> None:
        args: dict[str, Any] = dict(block.input)
        self.success_text = self._stable(str(args.get("success_text", "")))
        self.summary = str(args.get("summary") or args.get("reason", ""))

    def _stable(self, text: str) -> str:
        """Cut a success phrase at the first run-specific value.

        The model is asked for a phrase that reads the same every run, and mostly
        obliges - but "Account Opened! ... Your new account number: 13566" slipped
        through once, which would have made the check fail on every future call. The
        instruction is the fix; this is the guard, because a success condition that
        can only ever match one run is worse than no success condition at all.
        """
        cut = len(text)
        for value in [*self.params.values(), *(self.read_values)]:
            if value and (found := text.find(value)) != -1:
                cut = min(cut, found)
        trimmed = text[:cut].strip(" .,:;-!")
        return trimmed or text

    def _capability(self, goal: str, entry: str, app: str) -> Capability:
        used = {p for s in self.steps for p in re.findall(r"\{\{(\w+)\}\}", s.value or "")}
        inputs = [
            Input(name=n, type="string", secret=n in self.secrets)
            for n in [*self.params, *self.secrets]
            if n in used
        ]
        return Capability(
            id=_slug(goal, "capability").replace("capability_", "") or "capability",
            version=1,
            title=goal[:80],
            # Deliberately not trimmed. `_stable` exists to protect the success
            # matcher; applied to prose it cuts a sentence in half at the first
            # parameter value, which is worse than a description that names one run.
            description=self.summary or goal,
            target=Target(app=app, surface="browser", entry=entry),
            inputs=inputs,
            outputs=self.outputs,
            steps=self.steps,
            success=TextVisible(kind="text_visible", text=self.success_text, match="contains"),
            recorded=Recorded(
                at=datetime.now(UTC),
                run=self.evidence.run_id,
                model=MODEL,
                goal=goal,
                evidence=str(self.evidence.dir),
            ),
        )
