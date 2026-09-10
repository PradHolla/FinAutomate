"""What the agent is allowed to do, checked before every single action.

Two decisions are encoded here that are worth defending.

**The config is the authority on risk, not the model.** It is tempting to let the
model classify its own actions as safe or dangerous, and it would usually get it
right. But an agent that self-reports "this one is fine" is exactly the control
that fails when it matters. The model's opinion is recorded as a hint and compared
against the rule; the rule wins.

**Risk is a property of the control, not the action type.** "Click" is not
dangerous. Clicking the button that opens a bank account is. So the rule matches on
what the control is called, which is also what a person reading an audit log would
recognize.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

Verdict = Literal["allow", "risky", "deny"]


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    reason: str

    @property
    def blocked(self) -> bool:
        return self.verdict == "deny"


class Policy(BaseModel):
    """Loaded from config. Everything not permitted here is refused."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_path_prefix: str
    """Requests outside this path are refused. Keeps a wandering agent inside the
    one application it was pointed at."""

    allowed_actions: frozenset[str] = frozenset({"navigate", "click", "type", "select", "read"})

    denied_control_names: tuple[str, ...] = ()
    """Controls the agent must never touch, whatever it thinks. Administration
    screens and sign-out live here."""

    risky_control_names: tuple[str, ...] = ()
    """Controls that create, move, or destroy something. Permitted during discovery
    because that is the point of discovery, but recorded as risky so that unattended
    replay stops and asks a person."""

    def decide(self, action: str, control_name: str = "", role: str = "") -> Decision:
        if action not in self.allowed_actions:
            return Decision("deny", f"action {action!r} is not in the allowlist")

        name = control_name.strip().casefold()
        for denied in self.denied_control_names:
            if denied.casefold() in name and name:
                return Decision("deny", f"control {control_name!r} matches denied {denied!r}")

        # A link goes somewhere; it does not commit anything. In this application
        # the nav link and the submit button share the name "Open New Account", so
        # matching on the name alone marks a page visit as irreversible.
        if role != "link":
            for risky in self.risky_control_names:
                if risky.casefold() in name and name:
                    return Decision("risky", f"control {control_name!r} matches risky {risky!r}")

        return Decision("allow", "")

    def check_path(self, path: str) -> Decision:
        if "://" in path:
            return Decision("deny", "absolute URLs are refused; navigation is path-relative")
        if not path.startswith(self.allowed_path_prefix):
            return Decision("deny", f"{path!r} is outside {self.allowed_path_prefix!r}")
        return Decision("allow", "")


class Guards(BaseModel):
    """Limits on the run itself. A loop that talks to a paid API needs a ceiling
    that does not depend on the model choosing to stop."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_steps: int = Field(default=25, gt=0)
    max_seconds: int = Field(default=300, gt=0)
    max_tokens_total: int = Field(default=200_000, gt=0)


def load_policy(path: Path) -> tuple[Policy, Guards]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Policy.model_validate(raw["policy"]), Guards.model_validate(raw.get("guards", {}))
