"""Who is driving, and how control passes between them.

A lease: one field naming the current controller ("agent", "human", or "none"),
written to the intervention file so both the replaying worker and a person at a
terminal can read and check it before acting.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Controller = Literal["agent", "human", "none"]
Status = Literal["open", "approved", "handled", "rejected"]
"""`approved` is the machine acting with permission. `handled` is a person acting
instead of the machine, so the step is skipped. An audit cares which."""

TERMINAL: frozenset[str] = frozenset({"approved", "handled", "rejected"})


class HumanAction(BaseModel):
    """One thing a person did while they held the session."""

    model_config = ConfigDict(extra="forbid")

    at: datetime
    kind: str
    target: str
    value: str = ""


class Intervention(BaseModel):
    """A request for a person, and the record of what they decided."""

    model_config = ConfigDict(extra="forbid")

    id: str
    run: str
    capability: str
    step: str
    reason: str
    kind: Literal["risky_step"] = "risky_step"
    """Why a person was needed. One value, because there is one reason today: a step
    the recording marked irreversible came up with nobody watching.

    It stays as a field rather than being implied, so the record says why it exists
    rather than leaving a reader to infer it. It was briefly a two-value enum whose
    second value nothing ever produced, which is a promise the code did not keep."""

    controller: Controller = "human"
    status: Status = "open"

    screenshot: str | None = None
    screen: list[str] = Field(default_factory=list)
    """The controls visible when it stopped, in the same text form the model sees."""

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
        # Written to a neighbor file and moved into place, so a poller never
        # reads a half-written request.
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
                # Control returns to the agent on every path, including rejection -
                # otherwise the lease is left with a human who has walked away.
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
