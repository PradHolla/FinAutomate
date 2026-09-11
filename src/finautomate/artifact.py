"""The capability artifact: what a discovery run produces and a replay run consumes.

A capability is a UI flow recorded once and callable many times, like a function:
typed inputs, ordered steps, typed outputs, a success condition, and a declared list
of the business outcomes the caller needs to know about.

Two rules the types enforce rather than merely document:

  * No CSS selectors and no pixel coordinates. Controls are addressed the way a
    person or a screen reader addresses them - by role, name, and nearby text - so
    the same artifact shape works on a browser today and on a desktop accessibility
    API later.
  * No hostnames. `Target.entry` is a relative path; the base URL comes from tenant
    config at replay time. That is what lets one artifact serve many institutions.

The artifact never holds a secret value. `Input.secret` marks which parameter must
be wrapped in a redacting type when the caller supplies it at replay time.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PARAM_PATTERN = r"\{\{\s*(\w+)\s*\}\}"

MatchMode = Literal["exact", "prefix", "contains"]
"""How text is compared. Always after case-folding and collapsing whitespace.

`prefix` and `contains` exist for a concrete reason: ParaBank's funding-account
dropdown is anchored by the sentence "A minimum of $100.00 must be deposited...",
and that dollar figure is a configurable setting. An exact match would break the
moment an institution changed it.
"""


class Frozen(BaseModel):
    """Base for every artifact model. Unknown keys are an error, not a shrug."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------
# Locators
# --------------------------------------------------------------------------


# A `Scope` type - narrowing a search to one region - was designed and cut. Doing it
# honestly needs containment information (what is inside what), and a Snapshot carries
# reading order only. Faking it as "anything after this heading" would look like
# scoping without being scoping. Nothing in the current capability needs it. To add it
# back, give Control a parent path and resolve against that.


class RoleName(Frozen):
    """Role plus accessible name. The most robust strategy, and the first tried.

    Covers every button and link in ParaBank. Role is always paired with name
    because names repeat across roles - "Open New Account" is both a nav link and
    a submit button on the same page.
    """

    kind: Literal["role_name"]
    role: str
    name: str
    match: MatchMode = "exact"


class AnchoredRole(Frozen):
    """Role plus the nearest text before or after it.

    Required, not optional. Measured across eight of the target's screens: 42 form
    fields, and not one of them has an accessible name. The browser computes an
    empty string for every input, so `RoleName` cannot address a single one.
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
    """The element's `id`. Stable within one version of a vendor product, so it sits
    below the name- and anchor-based strategies rather than above them."""

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

    Replay tries each in turn and takes the first that matches exactly one element,
    then records which one won. That record is the drift signal: a step that used to
    resolve at strategy 0 and now resolves at strategy 2 is telling you the page
    changed under you.
    """

    description: str
    """Plain English, for a human reviewer and for the context sent to an operator
    when a run escalates. Not used for matching."""

    strategies: list[Strategy] = Field(min_length=1)


# --------------------------------------------------------------------------
# Checkpoints
# --------------------------------------------------------------------------


class ElementVisible(Frozen):
    """Waits for a control to be visible.

    Visibility rather than navigation is the primitive because the target app never
    navigates on submit - it hides one div and shows another at the same URL.
    """

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


# --------------------------------------------------------------------------
# Inputs and outputs
# --------------------------------------------------------------------------


class Input(Frozen):
    name: str
    type: Literal["string", "enum", "integer"] = "string"
    required: bool = True
    secret: bool = False
    """Marks a parameter that must never be written to an artifact, a log, or an
    evidence file. The artifact holds the flag; the value is wrapped in a redacting
    type when the caller supplies it."""
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


# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------

Action = Literal["navigate", "click", "type", "select", "read"]
Risk = Literal["safe", "risky"]

NEEDS_TARGET: frozenset[str] = frozenset({"click", "type", "select", "read"})
NEEDS_VALUE: frozenset[str] = frozenset({"type", "select"})


class Step(Frozen):
    id: str
    """Stable address for this step. Not decoration: per-institution overrides are
    sparse patches keyed by step id, and replay errors name the step that failed."""

    action: Action
    target: LocatorBundle | None = None
    value: str | None = None
    """A literal, or a `{{parameter}}` reference."""

    by: Literal["label", "value"] = "label"
    """For `select` only. Always label: ParaBank's account-type options carry the
    values "0" and "1" behind the text CHECKING and SAVINGS, and positional codes
    like that do not survive a version change."""

    risk: Risk = "safe"
    """`risky` means irreversible - it creates or moves something real. Unattended
    replay stops at a risky step and escalates to a person."""

    expect: Checkpoint | None = None
    """Optional per-step check. Turns "the click silently did nothing" into an error
    that names the step."""

    outcomes: dict[str, str] = Field(default_factory=dict)
    """Maps a step-level condition to a declared outcome name, e.g.
    `{"option_not_found": "FUNDING_ACCOUNT_NOT_FOUND"}`.

    This is what separates "the dropdown is missing" (hard failure - wrong page)
    from "the dropdown is there but has no such option" (business outcome - the app
    is telling us that account is not available to this customer)."""

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


# --------------------------------------------------------------------------
# Outcomes
# --------------------------------------------------------------------------

Classification = Literal["business_outcome", "recoverable", "hard_failure"]
"""The three classes the replay result contract must keep apart.

business_outcome  a legitimate answer the caller needs, not a crash
recoverable       retry, dismiss, or re-authenticate, within bounds
hard_failure      stop and report enough to debug it
"""


class Recovery(Frozen):
    action: Literal["restart"]
    """Go back to the entry point and run the flow again from its first step.

    There used to be a `restart_from: <step id>` here, and dropping it made the
    design better rather than worse. Two reasons. A runtime condition like an
    expired session belongs to the *application*, not to one recorded flow, so it is
    declared in tenant config - and config cannot name a step id, because every
    capability names its steps differently. And for the conditions that actually
    occur, resuming from the middle is wrong anyway: once a session is gone, every
    screen after the login page is gone with it.
    """

    max_attempts: int = Field(default=1, ge=1, le=3)
    """Bounded on purpose. Unbounded retry is how automation quietly hammers a
    production system."""


class Outcome(Frozen):
    name: str
    classification: Classification
    message: str | None = None
    detect: Checkpoint | None = None
    """How to spot it on the page. Omitted when a step raises it directly through
    its own `outcomes` map."""
    recovery: Recovery | None = None

    @model_validator(mode="after")
    def recovery_only_when_recoverable(self) -> Self:
        if self.recovery and self.classification != "recoverable":
            raise ValueError(
                f"outcome {self.name!r} declares recovery but is {self.classification!r}"
            )
        # The other direction, which is the one that bites: "recoverable" with no
        # recovery block is a promise the engine cannot keep. It would be detected,
        # classified as retryable, and then silently fall through to a hard failure
        # with no explanation. Rejecting it here means the engine never has to ask.
        if self.classification == "recoverable" and not self.recovery:
            raise ValueError(f"outcome {self.name!r} is recoverable but declares no recovery")
        if self.classification == "recoverable" and self.detect is None:
            raise ValueError(f"outcome {self.name!r} is recoverable but declares no detector")
        return self


# --------------------------------------------------------------------------
# Top level
# --------------------------------------------------------------------------


class Target(Frozen):
    app: str
    """The vendor product, not the institution. Many tenants run the same product."""

    surface: Literal["browser", "windows_uia", "macos_ax"] = "browser"
    """The seam between how we perceive a screen and what was recorded. Change this
    and the steps keep their shape, because role, name, and nearby text all exist in
    Windows UI Automation and macOS Accessibility too."""

    entry: str
    """Relative path. See the hostname rule below."""

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


class Capability(Frozen):
    schema_version: Literal[1] = 1
    """The format's version. Separate from `version` so that "the format changed"
    stays distinguishable from "someone re-recorded the flow"."""

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

    # -- cross-field checks: these catch real authoring mistakes ------------

    @model_validator(mode="after")
    def references_resolve(self) -> Self:
        step_ids = [s.id for s in self.steps]
        if len(step_ids) != len(set(step_ids)):
            dupes = {i for i in step_ids if step_ids.count(i) > 1}
            raise ValueError(f"duplicate step ids: {sorted(dupes)}")
        known_steps = set(step_ids)
        known_inputs = {i.name for i in self.inputs}
        known_outcomes = {o.name for o in self.outcomes}

        for out in self.outputs:
            if out.from_step not in known_steps:
                raise ValueError(f"output {out.name!r} reads from unknown step {out.from_step!r}")

        for step in self.steps:
            for condition, outcome_name in step.outcomes.items():
                if outcome_name not in known_outcomes:
                    raise ValueError(
                        f"step {step.id!r} maps {condition!r} to undeclared "
                        f"outcome {outcome_name!r}"
                    )
            for param in referenced_params(step.value):
                if param not in known_inputs:
                    raise ValueError(f"step {step.id!r} uses {{{{{param}}}}} but no such input")

        # Recovery used to name the step to resume from, and this checked the name
        # existed. It restarts the whole flow now, so there is no name to get wrong.
        return self

    @model_validator(mode="after")
    def every_input_is_used(self) -> Self:
        """Each declared input must be referenced by some step.

        Catches dead parameters, and matters most for secrets: a secret can only
        reach the browser through `{{parameter}}` substitution, so a declared but
        unreferenced secret means the flow is getting it some other way.

        Note what this deliberately does not claim to do. Nothing here can detect a
        password pasted into a step as a literal - the artifact has no way to know
        that "hunter2" is a secret. Keeping secret values out is enforced by the
        caller wrapping them in a redacting type, not by inspecting this file.
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


# --------------------------------------------------------------------------
# Load and save
# --------------------------------------------------------------------------


def load_capability(path: Path) -> Capability:
    """Read a capability from YAML. `safe_load` because artifacts are data, never code."""
    with path.open(encoding="utf-8") as fh:
        return Capability.model_validate(yaml.safe_load(fh))


def dump_capability(capability: Capability, path: Path) -> None:
    """Write a capability to YAML, keeping declaration order so diffs stay readable."""
    payload = capability.model_dump(mode="json", exclude_none=True, exclude_defaults=False)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(payload, fh, sort_keys=False, allow_unicode=True, width=88)
