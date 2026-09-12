"""The capability artifact: what a discovery run produces and a replay run consumes.

A capability is a UI flow recorded once and callable many times: typed inputs,
ordered steps, typed outputs, a success condition, and declared business outcomes.
Controls are addressed by role, name, and nearby text, never by CSS selector or
pixel coordinate, so the same shape works on a browser today and on a desktop
accessibility API later. `Target.entry` is a relative path; the base URL comes
from tenant config, so one artifact can serve many institutions.

The artifact never holds a secret value. `Input.secret` only marks which
parameter must be wrapped in a redacting type when the caller supplies it.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PARAM_PATTERN = r"\{\{\s*(\w+)\s*\}\}"

MatchMode = Literal["exact", "prefix", "contains"]
"""How text is compared, after case-folding and collapsing whitespace.

`prefix` and `contains` exist because ParaBank's funding-account dropdown is
anchored by a sentence with a configurable dollar figure; an exact match would
break when that figure changed.
"""


class Frozen(BaseModel):
    """Base for every artifact model. Unknown keys are an error, not a shrug."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class RoleName(Frozen):
    """Role plus accessible name, the most robust strategy.

    Role is paired with name because names repeat across roles: "Open New Account"
    is both a nav link and a submit button.
    """

    kind: Literal["role_name"]
    role: str
    name: str
    match: MatchMode = "exact"


class AnchoredRole(Frozen):
    """Role plus the nearest text before or after it.

    Required: 42 form fields across eight screens in the target app have no
    accessible name, so `RoleName` cannot address them.
    """

    kind: Literal["anchored_role"]
    role: str
    anchor: str
    match: MatchMode = "exact"
    position: Literal["after", "before"] = "after"


class FieldName(Frozen):
    """The form field's `name` attribute. On desktop, the control's automation id."""

    kind: Literal["field_name"]
    name: str


class FieldId(Frozen):
    """The element's `id`. Stable within one vendor version, below name- and
    anchor-based strategies rather than above them."""

    kind: Literal["field_id"]
    id: str


class TextContent(Frozen):
    """Matches on visible text. Used for headings and confirmation banners."""

    kind: Literal["text"]
    text: str
    match: MatchMode = "exact"


Strategy = Annotated[
    RoleName | AnchoredRole | FieldName | FieldId | TextContent,
    Field(discriminator="kind"),
]


class LocatorBundle(Frozen):
    """Ordered ways to find one control, most robust first.

    Replay tries each in turn and uses the first that matches exactly one element,
    then records which one won. That is the drift signal.
    """

    description: str
    """For a human reviewer. Not used for matching."""

    strategies: list[Strategy] = Field(min_length=1)


class ElementVisible(Frozen):
    """Waits for a control to be visible. Visibility, not navigation, because the
    target app never changes URL on submit; it hides one div and shows another."""

    kind: Literal["element_visible"]
    target: LocatorBundle
    timeout_ms: int = Field(default=10_000, gt=0)


class TextVisible(Frozen):
    """Waits for text to appear anywhere on the page."""

    kind: Literal["text_visible"]
    text: str
    match: MatchMode = "contains"
    timeout_ms: int = Field(default=10_000, gt=0)


Checkpoint = Annotated[ElementVisible | TextVisible, Field(discriminator="kind")]


class Input(Frozen):
    name: str
    type: Literal["string", "enum", "integer"] = "string"
    required: bool = True
    secret: bool = False
    """Marks a value that must never be written to an artifact, log, or evidence file."""
    values: list[str] | None = None
    description: str | None = None

    @model_validator(mode="after")
    def enum_needs_values(self) -> Self:
        if self.type == "enum" and not self.values:
            raise ValueError(f"input {self.name!r} is an enum but declares no values")
        if self.type != "enum" and self.values:
            raise ValueError(f"input {self.name!r} declares values but is not an enum")
        return self


class Output(Frozen):
    name: str
    type: Literal["string", "integer"] = "string"
    from_step: str
    """The id of the `read` step that produces this value."""
    description: str | None = None


Action = Literal["navigate", "click", "type", "select", "read"]
Risk = Literal["safe", "risky"]

NEEDS_TARGET: frozenset[str] = frozenset({"click", "type", "select", "read"})
NEEDS_VALUE: frozenset[str] = frozenset({"type", "select"})


class Step(Frozen):
    id: str
    """Stable id. Per-institution overrides and replay errors key off this."""

    action: Action
    target: LocatorBundle | None = None
    value: str | None = None
    """A literal, or a `{{parameter}}` reference."""

    by: Literal["label", "value"] = "label"
    """For `select` only. Defaults to label because ParaBank's account-type
    options are "0" and "1" behind the text CHECKING and SAVINGS."""

    risk: Risk = "safe"
    """`risky` means irreversible. Unattended replay stops at a risky step and
    escalates to a person."""

    expect: Checkpoint | None = None
    """Optional per-step check, so a silent no-op becomes a named error."""

    outcomes: dict[str, str] = Field(default_factory=dict)
    """Maps a step-level condition to a declared outcome, e.g.
    `{"option_not_found": "FUNDING_ACCOUNT_NOT_FOUND"}`. Separates a missing
    dropdown (hard failure) from an empty one (business outcome)."""

    description: str | None = None

    @model_validator(mode="after")
    def action_shape(self) -> Self:
        if self.action in NEEDS_TARGET and self.target is None:
            raise ValueError(f"step {self.id!r}: action {self.action!r} needs a target")
        if self.action == "navigate" and self.target is not None:
            raise ValueError(f"step {self.id!r}: navigate takes no target")
        if self.action in NEEDS_VALUE and self.value is None:
            raise ValueError(f"step {self.id!r}: action {self.action!r} needs a value")
        return self


Classification = Literal["business_outcome", "recoverable", "hard_failure"]
"""The three classes the replay result keeps apart.

business_outcome  a legitimate answer, not a crash
recoverable       retry, dismiss, or re-authenticate, within bounds
hard_failure      stop and report enough to debug it
"""


class Recovery(Frozen):
    """What to do about a recoverable condition."""

    action: Literal["restart", "dismiss"]
    """`restart` goes back to the entry point and runs the flow again. It is the only
    honest response to an expired session, since every screen after the login page is
    gone with it.

    `dismiss` clicks something and retries the step that failed. That is the shape of an
    interstitial: a banner or modal appeared over the page, and the flow underneath is
    still intact."""

    target: LocatorBundle | None = None
    """What to click, for `dismiss`. Addressed by the same ladder as any other control,
    because a consent banner is relabeled by a rebrand like everything else."""

    max_attempts: int = Field(default=1, ge=1, le=3)
    """Bounded on purpose. Unbounded retry is how automation hammers a production
    system."""

    @model_validator(mode="after")
    def dismiss_needs_a_target(self) -> Self:
        if self.action == "dismiss" and self.target is None:
            raise ValueError("a dismiss recovery must say what to dismiss")
        if self.action == "restart" and self.target is not None:
            raise ValueError("a restart recovery takes no target")
        return self


class Outcome(Frozen):
    name: str
    classification: Classification
    message: str | None = None
    detect: Checkpoint | None = None
    """How to spot it on the page. Omitted when a step raises it via `outcomes`."""

    explain: LocatorBundle | None = None
    """Where the application states its own reason, when it gives one.

    Read at the moment the outcome is detected and appended to the message, so a
    caller learns why it was refused rather than only that it was. Optional, because
    plenty of outcomes have nothing more to say and an outcome raised by a step has
    no page to read."""

    recovery: Recovery | None = None

    @model_validator(mode="after")
    def recovery_only_when_recoverable(self) -> Self:
        if self.recovery and self.classification != "recoverable":
            raise ValueError(
                f"outcome {self.name!r} declares recovery but is {self.classification!r}"
            )
        # "recoverable" with no recovery block is a promise the engine can't keep.
        if self.classification == "recoverable" and not self.recovery:
            raise ValueError(f"outcome {self.name!r} is recoverable but declares no recovery")
        if self.classification == "recoverable" and self.detect is None:
            raise ValueError(f"outcome {self.name!r} is recoverable but declares no detector")
        return self


class Target(Frozen):
    app: str
    """The vendor product, not the institution. Many tenants run the same product."""

    surface: Literal["browser", "windows_uia", "macos_ax"] = "browser"
    """Changing this keeps the steps' shape, since role, name, and nearby text
    exist in Windows UI Automation and macOS Accessibility too."""

    entry: str
    """Relative path; the base URL comes from tenant config."""

    app_version: str | None = None

    @field_validator("entry")
    @classmethod
    def entry_is_relative(cls, v: str) -> str:
        if "://" in v or v.startswith("//"):
            raise ValueError(
                f"entry must be a relative path, got {v!r}. The base URL belongs to "
                "tenant config, so that one artifact can serve many institutions."
            )
        return v


class Recorded(Frozen):
    """Provenance. Links to the discovery run without embedding its transcript."""

    at: datetime
    run: str
    model: str
    goal: str
    evidence: str | None = None
    supersedes: str | None = None
    """The run this one re-recorded, so a version chain can be followed back."""


class Capability(Frozen):
    schema_version: Literal[1] = 1
    """Format version, separate from `version` so a format change and a
    re-record stay distinguishable."""

    id: str
    version: int = Field(ge=1)
    """This recording's revision. Bumped on re-record."""

    title: str
    description: str

    target: Target
    inputs: list[Input] = Field(default_factory=list)
    outputs: list[Output] = Field(default_factory=list)
    steps: list[Step] = Field(min_length=1)
    success: Checkpoint
    outcomes: list[Outcome] = Field(default_factory=list)
    recorded: Recorded | None = None

    @model_validator(mode="after")
    def step_ids_are_unique(self) -> Self:
        ids = [s.id for s in self.steps]
        if len(ids) != len(set(ids)):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"duplicate step ids: {dupes}")
        return self

    @model_validator(mode="after")
    def outputs_read_from_real_steps(self) -> Self:
        known = {s.id for s in self.steps}
        for out in self.outputs:
            if out.from_step not in known:
                raise ValueError(f"output {out.name!r} reads from unknown step {out.from_step!r}")
        return self

    @model_validator(mode="after")
    def steps_reference_declared_names(self) -> Self:
        known_inputs = {i.name for i in self.inputs}
        known_outcomes = {o.name for o in self.outcomes}
        for step in self.steps:
            for condition, outcome in step.outcomes.items():
                if outcome not in known_outcomes:
                    raise ValueError(
                        f"step {step.id!r} maps {condition!r} to undeclared outcome {outcome!r}"
                    )
            for param in referenced_params(step.value):
                if param not in known_inputs:
                    raise ValueError(f"step {step.id!r} uses {{{{{param}}}}} but no such input")
        return self

    @model_validator(mode="after")
    def every_input_is_used(self) -> Self:
        """Each declared input must be used by some step.

        Catches dead parameters, and for a secret an unreferenced one means the value
        is reaching the page some other way. It cannot catch a password pasted in as a
        literal: nothing here knows that "hunter2" is a secret.
        """
        used: set[str] = set()
        for step in self.steps:
            used.update(referenced_params(step.value))
        unused = sorted({i.name for i in self.inputs} - used)
        if unused:
            raise ValueError(f"inputs declared but never used: {unused}")
        return self

    def step(self, step_id: str) -> Step:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise KeyError(f"no step {step_id!r} in capability {self.id!r}")


def referenced_params(value: str | None) -> list[str]:
    """Parameter names referenced by a step value, e.g. "{{member_id}}" -> ["member_id"]."""
    return re.findall(PARAM_PATTERN, value) if value else []


def contract_diff(prior: Capability, fresh: Capability) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Names a caller binds to, added and removed between two recordings.

    Steps, locators and checkpoints are deliberately not compared. A re-record is
    expected to change those, and the version is about the contract, not the route
    taken through the screens.
    """
    was, now = _contract(prior), _contract(fresh)
    return tuple(sorted(now - was)), tuple(sorted(was - now))


def _contract(capability: Capability) -> set[str]:
    """Every name a caller binds to, tagged by which side of the call it is on.

    Tagged because an input and an output may share a name, and trading one for the
    other breaks a caller in a way an untagged comparison would report as no change.
    """
    return {f"input {i.name}" for i in capability.inputs} | {
        f"output {o.name}" for o in capability.outputs
    }


def load_capability(path: Path) -> Capability:
    """Read a capability from YAML. `safe_load` because artifacts are data, never code."""
    with path.open(encoding="utf-8") as fh:
        return Capability.model_validate(yaml.safe_load(fh))


def dump_capability(capability: Capability, path: Path) -> None:
    """Write a capability to YAML, keeping declaration order so diffs stay readable."""
    payload = capability.model_dump(mode="json", exclude_none=True, exclude_defaults=False)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(payload, fh, sort_keys=False, allow_unicode=True, width=88)
