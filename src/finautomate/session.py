"""Who is driving, and how control passes between them.

The brief asks for a way to know who is - or should be - in control. That is a
lease: a single field naming the current controller, written where both sides can
see it, and checked before either acts.

    agent   automation is driving
    human   automation has stopped and is waiting; a person has the browser
    none    nobody is driving; the run is over

The lease lives in the intervention file rather than in memory because the two
parties are not in the same process. The replaying worker holds the browser; the
operator is a person at a terminal. A file both can read is the smallest thing that
works, and it is also what a real deployment would need - the operator console and
the worker are never the same process there either.

**What a person is asked to decide.** Two different things, kept apart on purpose:

    approve   let the automation perform the step it stopped at
    handled   the person did it themselves; skip that step and carry on

Collapsing those into one "continue" would lose the distinction between a machine
acting with permission and a human acting instead of the machine - which is exactly
what an audit of a bank's systems would want to see.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Controller = Literal["agent", "human", "none"]
Status = Literal["open", "approved", "handled", "rejected"]

TERMINAL: frozenset[str] = frozenset({"approved", "handled", "rejected"})


class HumanAction(BaseModel):
    """One thing a person did while they held the session."""

    model_config = ConfigDict(extra="forbid")

    at: datetime
    kind: str
    target: str
    value: str = ""


class Intervention(BaseModel):
    """A request for a person, and the record of what they decided.

    Carries what the brief asks an intervention to carry: which capability, which
    step, the state of the screen, and why it stopped.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    run: str
    capability: str
    step: str
    reason: str
    kind: Literal["risky_step", "unrecoverable"] = "risky_step"

    controller: Controller = "human"
    status: Status = "open"

    screenshot: str | None = None
    screen: list[str] = Field(default_factory=list)
    """The controls visible when it stopped, in the same text form the discovery
    model sees. An operator reading this knows what the automation was looking at
    without needing the browser."""

    created: datetime = Field(default_factory=lambda: datetime.now(UTC))
    resolved: datetime | None = None
    operator: str | None = None
    note: str | None = None
    human_actions: list[HumanAction] = Field(default_factory=list)

    @property
    def open(self) -> bool:
        return self.status == "open"


class InterventionStore:
    """A directory of intervention files. The operator's inbox."""

    def __init__(self, root: Path = Path("interventions")) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, intervention_id: str) -> Path:
        return self.root / f"{intervention_id}.json"

    def write(self, intervention: Intervention) -> Path:
        target = self.path(intervention.id)
        # Written to a neighbouring file and moved into place, so a worker polling
        # this directory never reads a half-written request.
        scratch = target.with_suffix(".writing")
        scratch.write_text(intervention.model_dump_json(indent=2), encoding="utf-8")
        scratch.replace(target)
        return target

    def read(self, intervention_id: str) -> Intervention:
        raw = json.loads(self.path(intervention_id).read_text(encoding="utf-8"))
        return Intervention.model_validate(raw)

    def pending(self) -> list[Intervention]:
        out = [
            Intervention.model_validate(json.loads(p.read_text(encoding="utf-8")))
            for p in sorted(self.root.glob("*.json"))
        ]
        return [i for i in out if i.open]

    def resolve(
        self,
        intervention_id: str,
        status: Status,
        *,
        operator: str | None = None,
        note: str | None = None,
    ) -> Intervention:
        current = self.read(intervention_id)
        if not current.open:
            raise ValueError(f"{intervention_id} was already {current.status}")
        updated = current.model_copy(
            update={
                "status": status,
                # Control returns to the automation on every path. Even a rejection
                # hands back, because the worker still has to unwind cleanly and
                # report - leaving the lease with a human who has walked away is how
                # a run hangs forever.
                "controller": "agent",
                "resolved": datetime.now(UTC),
                "operator": operator,
                "note": note,
            }
        )
        self.write(updated)
        return updated

    def record_actions(self, intervention_id: str, actions: list[dict[str, Any]]) -> None:
        """Append what the person did while they held the session."""
        current = self.read(intervention_id)
        captured = [
            HumanAction(
                at=datetime.now(UTC),
                kind=str(a.get("kind", "")),
                target=str(a.get("target", ""))[:120],
                value=str(a.get("value", ""))[:120],
            )
            for a in actions
        ]
        self.write(
            current.model_copy(update={"human_actions": [*current.human_actions, *captured]})
        )
