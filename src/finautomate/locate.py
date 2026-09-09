"""Turning a recorded locator into one control on screen.

This is where the artifact meets reality, and it is deliberately a pure function
over a `Snapshot`. No browser, no page, no I/O - so every rule below can be tested
in milliseconds against a handmade snapshot.

Two rules that carry most of the weight:

  * **First unique match wins.** Strategies are tried in the recorded order and the
    first one matching exactly one control is used.
  * **Ambiguity is failure, not a coin flip.** A strategy matching two controls
    resolves nothing and falls through. Clicking one of two candidates is how
    automation quietly does the wrong thing in production.

Every attempt is recorded, including the ones that missed. That trail is both the
debuggable error the caller gets on failure and the drift signal on success: a step
that used to resolve at strategy 0 and now resolves at strategy 2 is telling you the
page changed under you.
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
    """Case-fold and collapse whitespace.

    Applied to both sides of every text comparison, so a page that gains an extra
    space or changes capitalization does not break a locator.
    """
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

        Not an error - the ladder did its job - but worth surfacing, because it is
        the earliest warning that the page has drifted.
        """
        return self.strategy_index is not None and self.strategy_index > 0


def resolve(bundle: LocatorBundle, snapshot: Snapshot) -> Resolution:
    attempts: list[Attempt] = []
    for index, strategy in enumerate(bundle.strategies):
        found = _candidates(strategy, snapshot)
        refs = tuple(c.ref for c in found)
        if len(found) == 1:
            attempts.append(Attempt(index, strategy.kind, refs))
            return Resolution(found[0], index, strategy.kind, tuple(attempts))
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


# --------------------------------------------------------------------------
# One function per strategy kind
# --------------------------------------------------------------------------


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
            # Attribute values are compared exactly. They are identifiers, not prose,
            # so normalizing them would be wrong.
            return [c for c in snapshot.controls if c.field_name and c.field_name == strategy.name]
        case FieldId():
            return [c for c in snapshot.controls if c.field_id and c.field_id == strategy.id]
        case TextContent():
            return [
                c for c in snapshot.controls if text_matches(c.text, strategy.text, strategy.match)
            ]
        case _:
            # Reached only if a strategy kind is added above and not handled here,
            # or if something that is not a Strategy is passed in. Both used to
            # return None silently and blow up somewhere unrelated.
            assert_never(strategy)


def _anchored(strategy: AnchoredRole, snapshot: Snapshot) -> list[Control]:
    """The control of the right role nearest to a piece of text.

    "Nearest" is measured in reading order, not pixels. Reading order survives a
    restyle, and it is the one ordering that exists on a desktop control tree too.

    If the anchor text appears more than once, each occurrence contributes its own
    nearest control. They are deduplicated, so several anchors pointing at the same
    control still resolve; several anchors pointing at different controls stay
    ambiguous and the strategy correctly fails.
    """
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
