"""What the agent is allowed to do, checked before every action.

The config decides what is risky, not the model: its own opinion is recorded as a
hint but the rule wins. Risk is a property of the named control, not the action
type, since "click" is not dangerous but clicking the button that opens an account is.
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


def _matches(pattern: str, name: str, role: str) -> bool:
    """Whether a rule names this control.

    A rule is a name, optionally qualified by role as `button:Open New Account`.
    The qualifier exists because applications reuse one label between a menu item and
    the button it leads to - the target app does exactly that - so an unqualified rule
    meant for the button silently blocks the link as well. An unqualified rule still
    matches any role, which is usually what you want for something like Log Out.
    """
    wanted_role, _, wanted = pattern.rpartition(":")
    if wanted_role and role and wanted_role.strip().casefold() != role.strip().casefold():
        return False
    return bool(name) and wanted.strip().casefold() in name


class Policy(BaseModel):
    """Loaded from config. Everything not permitted here is refused."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_path_prefix: str
    """Requests outside this path are refused."""

    allowed_actions: frozenset[str] = frozenset({"navigate", "click", "type", "select", "read"})

    denied_control_names: tuple[str, ...] = ()
    """Controls the agent must never touch. Administration screens, sign-out."""

    denied_paths: tuple[str, ...] = ()
    """Paths the agent must never navigate to.

    Not redundant with `denied_control_names`. A discovery run found the hole: told to
    sign out, the model was refused the Log Out control and then navigated straight to
    `/parabank/logout.htm` instead. Blocking a control does nothing if the page behind
    it is one URL away."""

    risky_control_names: tuple[str, ...] = ()
    """Controls that create, move, or destroy something. Allowed during discovery,
    but flagged so unattended replay stops and asks a person."""

    def decide(self, action: str, control_name: str = "", role: str = "") -> Decision:
        if action not in self.allowed_actions:
            return Decision("deny", f"action {action!r} is not in the allowlist")

        name = control_name.strip().casefold()
        for denied in self.denied_control_names:
            if _matches(denied, name, role):
                return Decision("deny", f"control {control_name!r} matches denied {denied!r}")

        # A link only navigates. Here the nav link and the submit button share the
        # name "Open New Account", so matching on name alone would flag a page visit.
        if role != "link":
            for risky in self.risky_control_names:
                if _matches(risky, name, role):
                    return Decision("risky", f"control {control_name!r} matches risky {risky!r}")

        return Decision("allow", "")

    def commits(self, control_name: str, role: str = "") -> bool:
        """Whether this control is one that creates, moves or destroys something.

        Asked separately from `decide` because a control can be both risky and denied,
        and the denial answers first. What is risky about it still matters: it is the
        last moment anything on the form can still be changed.

        A link never commits, for the same reason it is never risky: it only navigates,
        and applications label the menu item and the button it leads to identically.
        Without this the one-shot warning was spent on the link and never reached the
        button, which is the moment it exists for.
        """
        if role == "link":
            return False
        name = control_name.strip().casefold()
        return any(_matches(r, name, role) for r in self.risky_control_names)

    def check_path(self, path: str) -> Decision:
        if "://" in path:
            return Decision("deny", "absolute URLs are refused; navigation is path-relative")
        if not path.startswith(self.allowed_path_prefix):
            return Decision("deny", f"{path!r} is outside {self.allowed_path_prefix!r}")
        target = path.split("?")[0].split(";")[0].casefold()
        for denied in self.denied_paths:
            if denied.casefold() in target:
                return Decision("deny", f"path {path!r} matches denied {denied!r}")
        return Decision("allow", "")


class Guards(BaseModel):
    """Limits on the run itself, independent of the model choosing to stop."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_steps: int = Field(default=25, gt=0)
    max_seconds: int = Field(default=300, gt=0)
    max_tokens_total: int = Field(default=200_000, gt=0)


def load_policy(path: Path) -> tuple[Policy, Guards]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Policy.model_validate(raw["policy"]), Guards.model_validate(raw.get("guards", {}))
