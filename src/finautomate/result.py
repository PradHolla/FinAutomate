"""What a replay hands back to whoever called it.

The brief names one mistake as the most common in this problem: treating "no such
member" as a crash. It is not a crash, it is the answer. So the four outcomes below
are four distinct types rather than a status string, and the caller has to
acknowledge which one it got before it can read anything out of it.

    Success          it worked; here are the declared outputs
    BusinessOutcome  the application answered, and the answer was no
    NeedsHuman       stopped deliberately at a step a person must approve
    HardFailure      something is broken; here is what to look at

There is a fifth class that never reaches the caller. A *recoverable* condition -
an expired session, a page still loading - is retried inside the engine within the
bounds the artifact declares. Surfacing it would tell the caller about our plumbing
rather than about their request.
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
    error - but it is the earliest warning that the page has drifted, and the only
    one you get before the fallback runs out too."""

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
    """A legitimate answer the caller needs. Not a failure.

    The application was reached, understood the request, and said no - the account
    does not exist, the customer is not eligible. A caller that retries this is
    wrong, which is why it is not shaped like an error.
    """

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
    """Something is wrong with the automation or the application.

    Carries what the brief asks for: which step, what was expected, what was seen.
    """

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

A business outcome is deliberately not 0 and not 1. It did not succeed, so a caller
must not treat it as success; nothing is broken, so a caller must not page anyone.
Two is the honest answer, and 3 says a person is required.
"""
