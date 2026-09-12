"""Turns a recorded locator into one control on screen.

A pure function over a `Snapshot`: no browser, no I/O, so every rule here is
testable against a handmade snapshot. Strategies are tried in the recorded order;
the first one matching exactly one control wins, and every attempt is kept for the
error message and as a drift signal.
"""

import re
from dataclasses import dataclass
from typing import assert_never

from finautomate.artifact import (
    AnchoredRole,
    FieldId,
    FieldName,
    LocatorBundle,
    MatchMode,
    RoleName,
    Strategy,
    TextContent,
)
from finautomate.surface.models import Control, Snapshot

_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Case-fold and collapse whitespace, so an extra space or a case change
    doesn't break a locator."""
    return _WHITESPACE.sub(" ", text).strip().casefold()


def text_matches(candidate: str, wanted: str, mode: MatchMode) -> bool:
    got, want = normalize(candidate), normalize(wanted)
    if mode == "exact":
        return got == want
    if mode == "prefix":
        return got.startswith(want)
    return want in got


@dataclass(frozen=True)
class Attempt:
    """What one strategy found. Kept whether it succeeded or not."""

    index: int
    kind: str
    matched: tuple[str, ...]
    note: str = ""


@dataclass(frozen=True)
class Resolution:
    control: Control | None
    strategy_index: int | None
    strategy_kind: str | None
    attempts: tuple[Attempt, ...]

    @property
    def found(self) -> bool:
        return self.control is not None

    @property
    def used_fallback(self) -> bool:
        """True when the preferred strategy missed and a lower one caught it.
        Not an error, but the earliest sign the page has drifted."""
        return self.strategy_index is not None and self.strategy_index > 0


def resolve(bundle: LocatorBundle, snapshot: Snapshot) -> Resolution:
    attempts: list[Attempt] = []
    for index, strategy in enumerate(bundle.strategies):
        found = _candidates(strategy, snapshot)
        refs = tuple(c.ref for c in found)
        if len(found) == 1:
            attempts.append(Attempt(index, strategy.kind, refs))
            return Resolution(found[0], index, strategy.kind, tuple(attempts))
        # Two matches is a failure, not a coin flip between them.
        note = "no match" if not found else f"ambiguous, {len(found)} matches"
        attempts.append(Attempt(index, strategy.kind, refs, note))
    return Resolution(None, None, None, tuple(attempts))


def explain(bundle: LocatorBundle, resolution: Resolution) -> str:
    """A failure message someone can act on: what we wanted, what each way found."""
    lines = [f"could not find {bundle.description!r}"]
    for attempt in resolution.attempts:
        detail = attempt.note or f"matched {attempt.matched[0]}"
        lines.append(f"  [{attempt.index}] {attempt.kind}: {detail}")
    return "\n".join(lines)


def text_present(text: str, mode: MatchMode, snapshot: Snapshot) -> bool:
    """Whether text appears anywhere on screen. Backs the text checkpoints."""
    return any(text_matches(a.text, text, mode) for a in snapshot.anchors) or any(
        text_matches(c.text, text, mode) for c in snapshot.controls
    )


def _candidates(strategy: Strategy, snapshot: Snapshot) -> list[Control]:
    match strategy:
        case RoleName():
            return [
                c
                for c in snapshot.controls
                if c.role == strategy.role and text_matches(c.name, strategy.name, strategy.match)
            ]
        case AnchoredRole():
            return _anchored(strategy, snapshot)
        case FieldName():
            # Compared exactly: field names are identifiers, not prose.
            return [c for c in snapshot.controls if c.field_name and c.field_name == strategy.name]
        case FieldId():
            return [c for c in snapshot.controls if c.field_id and c.field_id == strategy.id]
        case TextContent():
            return [
                c for c in snapshot.controls if text_matches(c.text, strategy.text, strategy.match)
            ]
        case _:
            # Exhaustiveness check: a new Strategy kind must be handled above.
            assert_never(strategy)


def _anchored(strategy: AnchoredRole, snapshot: Snapshot) -> list[Control]:
    """The control of the right role nearest to a piece of text, measured in
    reading order rather than pixels. Anchors are deduplicated by the control they
    land on, so anchors that disagree on the control stay ambiguous."""
    hits: dict[str, Control] = {}
    for anchor in snapshot.anchors:
        if not text_matches(anchor.text, strategy.anchor, strategy.match):
            continue
        pool = [c for c in snapshot.controls if c.role == strategy.role]
        if strategy.position == "after":
            pool = sorted(
                (c for c in pool if c.doc_order > anchor.doc_order),
                key=lambda c: c.doc_order,
            )
        else:
            pool = sorted(
                (c for c in pool if c.doc_order < anchor.doc_order),
                key=lambda c: -c.doc_order,
            )
        if pool:
            hits.setdefault(pool[0].ref, pool[0])
    return list(hits.values())
