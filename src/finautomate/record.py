"""Turns an action the model just took into something replay can repeat.

Proposes every strategy that could address the control just acted on, and keeps
only the ones verified by running the production resolver, so recorder and
replayer cannot disagree.
"""

import re
from typing import Literal

from finautomate.artifact import (
    AnchoredRole,
    FieldId,
    FieldName,
    LocatorBundle,
    RoleName,
    Strategy,
    TextContent,
)
from finautomate.locate import resolve
from finautomate.surface.models import Control, Snapshot, TextAnchor

MAX_ANCHORS = 5
"""How many nearby anchors to consider on each side of a control."""


def build_bundle(
    control: Control,
    snapshot: Snapshot,
    description: str,
    forbid: frozenset[str] = frozenset(),
) -> LocatorBundle | None:
    """Ordered ways to find `control` again, best first, or None if it can't be
    addressed. `forbid` is this run's own data; a locator containing it would
    only ever resolve for this run."""
    proposals = [s for s in _proposals(control, snapshot) if not _carries(s, forbid)]
    verified = [s for s in proposals if _resolves_to(s, control, snapshot)]
    if not verified:
        return None
    return LocatorBundle(description=description, strategies=verified)


def _carries(strategy: Strategy, forbid: frozenset[str]) -> bool:
    """Whether a strategy embeds any of this run's data."""
    payload = " ".join(
        str(getattr(strategy, field, "")) for field in ("name", "anchor", "text", "id")
    )
    return any(value and value in payload for value in forbid)


def _resolves_to(strategy: Strategy, control: Control, snapshot: Snapshot) -> bool:
    """The verification step. Runs the production resolver on a one-strategy bundle."""
    probe = LocatorBundle(description="probe", strategies=[strategy])
    outcome = resolve(probe, snapshot)
    return outcome.control is not None and outcome.control.ref == control.ref


def _proposals(control: Control, snapshot: Snapshot) -> list[Strategy]:
    """Every strategy worth testing, best first: role and name, then nearby
    text, then form attributes, then visible text last."""
    out: list[Strategy] = []

    # Skipped even before `forbid` can catch it: a checkpoint control can
    # appear before the value it's named after is known.
    if control.name and not _has_digit(control.name):
        out.append(RoleName(kind="role_name", role=control.role, name=control.name))

    out.extend(_anchored(control, snapshot))

    if control.field_name:
        out.append(FieldName(kind="field_name", name=control.field_name))
    if control.field_id and not _generated(control.field_id):
        out.append(FieldId(kind="field_id", id=control.field_id))

    # A digit means data, not a label, and would only match the run it came from.
    if control.text and control.text != control.name and not _has_digit(control.text):
        out.append(TextContent(kind="text", text=control.text))

    return out


def _has_digit(text: str) -> bool:
    return any(character.isdigit() for character in text)


_UUID = re.compile(r"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z", re.I)


def _generated(field_id: str) -> bool:
    """Whether an element id was minted for this page load rather than written by
    a developer.

    Verification cannot catch these. The id resolves perfectly on the snapshot it
    came from, and is gone by the next one. The target app stamps a fresh UUID on
    one of its Bill Pay fields on every load, which we found by loading the page
    three times and comparing.

    Only the UUID shape, because that is the one we have actually seen. A different
    generator would need its own rule, and the cost of missing one is a rung that
    never fires rather than a broken run - the ladder carries other ways to find
    the same control.
    """
    return bool(_UUID.match(field_id))


def _anchored(control: Control, snapshot: Snapshot) -> list[Strategy]:
    """Anchor to nearby text: above the control first, then below as a fallback
    for a control that's last of its role and so unreachable from above."""
    above = sorted(
        (a for a in snapshot.anchors if a.doc_order < control.doc_order),
        key=lambda a: -a.doc_order,
    )[:MAX_ANCHORS]
    below = sorted(
        (a for a in snapshot.anchors if a.doc_order > control.doc_order),
        key=lambda a: a.doc_order,
    )[:MAX_ANCHORS]

    out: list[Strategy] = []
    for anchor in above:
        out.extend(_anchor_strategies(anchor, "after", control.role))
    for anchor in below:
        out.extend(_anchor_strategies(anchor, "before", control.role))
    return out


def _anchor_strategies(
    anchor: TextAnchor, position: Literal["after", "before"], role: str
) -> list[Strategy]:
    """The strategies one anchor contributes."""
    out: list[Strategy] = []
    stable = _prefix_before_first_digit(anchor.text)
    if stable:
        out.append(
            AnchoredRole(
                kind="anchored_role", role=role, anchor=stable, match="prefix", position=position
            )
        )
    # Text or anchors with a digit are refused: they'd only match the run
    # (or day) that produced them.
    if not _has_digit(anchor.text):
        out.append(
            AnchoredRole(kind="anchored_role", role=role, anchor=anchor.text, position=position)
        )
    return out


MIN_PREFIX_CHARS = 8


def _prefix_before_first_digit(text: str) -> str | None:
    """The leading whole words of `text` before the first word with a digit.

    "A minimum of $100.00 must be deposited"  ->  "A minimum of"
    "Acct 12345"                              ->  None (too short)
    "Username"                                ->  None (no digit)
    """
    kept: list[str] = []
    for word in text.split():
        if any(character.isdigit() for character in word):
            prefix = " ".join(kept).rstrip(" $£€#:")
            return prefix if len(prefix) >= MIN_PREFIX_CHARS else None
        kept.append(word)
    return None
