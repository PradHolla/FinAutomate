"""What a replay hands back to the caller.

Four distinct outcome types instead of a status string: `Success`, `BusinessOutcome`
(the app answered no), `NeedsHuman` (stopped before something irreversible), and
`HardFailure`. A fifth, recoverable, condition is retried inside the engine and
never reaches the caller.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class StepRecord(BaseModel):
    """One executed step. `strategy_index` is the interesting field."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    action: str
    strategy_index: int | None = None
    strategy_kind: str | None = None
    used_fallback: bool = False
    """True when the preferred locator missed and a lower one caught it. Not an
    error, but the earliest warning that the page has drifted."""

    recovered_from: str | None = None
    duration_ms: int = 0


class Outcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    capability: str
    version: int
    steps: list[StepRecord] = Field(default_factory=list)
    duration_ms: int = 0
    evidence: str | None = None

    @property
    def drifting_steps(self) -> list[str]:
        return [s.id for s in self.steps if s.used_fallback]


class Success(Outcome):
    kind: Literal["success"] = "success"
    outputs: dict[str, str] = Field(default_factory=dict)


class BusinessOutcome(Outcome):
    """A legitimate answer, not a failure. The app was reached and said no,
    so retrying is wrong."""

    kind: Literal["business_outcome"] = "business_outcome"
    outcome: str
    message: str
    step: str


class NeedsHuman(Outcome):
    """Stopped on purpose, before doing something irreversible."""

    kind: Literal["needs_human"] = "needs_human"
    step: str
    reason: str
    intervention: str | None = None
    """Path to the intervention request written for the operator."""


class HardFailure(Outcome):
    """Something is wrong with the automation or the application."""

    kind: Literal["hard_failure"] = "hard_failure"
    step: str
    expected: str
    observed: str
    screenshot: str | None = None


Result = Annotated[
    Success | BusinessOutcome | NeedsHuman | HardFailure,
    Field(discriminator="kind"),
]

EXIT_CODES: dict[str, int] = {
    "success": 0,
    "hard_failure": 1,
    "business_outcome": 2,
    "needs_human": 3,
}
"""Exit codes a shell script can branch on.

A business outcome is not 0 or 1: it did not succeed, but nothing is broken either.
"""
