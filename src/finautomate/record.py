"""Turning an action the model just took into something replay can repeat.

The model works in refs - "click c7". A ref is meaningless five seconds later, so
at the moment of every action we have to answer a harder question: given this
control, what are all the ways to find it again, and which will hold up best?

The method is deliberately not clever. Propose every strategy that could address
this control, then **verify each one by running the real resolver against the same
snapshot** and keeping only those that come back with this exact control and no
other. Two things follow:

  * The recorder cannot emit a locator the resolver disagrees with. They are the
    same code path, so a bundle that validates here works at replay time.
  * Ambiguity is caught at record time rather than at three in the morning. If a
    strategy would match two controls, it never reaches the artifact.
"""

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
from finautomate.surface.models import Control, Snapshot

MAX_ANCHORS = 5
"""How far back to look for a text anchor. Anchors far above a control are usually
section headings that happen to precede it rather than describe it."""


def build_bundle(
    control: Control,
    snapshot: Snapshot,
    description: str,
    forbid: frozenset[str] = frozenset(),
) -> LocatorBundle | None:
    """Ordered ways to find `control` again, best first. None if it cannot be
    addressed at all - a control with no name, no anchor, no id and no text.

    `forbid` is this run's own data: the parameters supplied and the values read
    off the screen. A locator containing any of it would resolve perfectly on the
    run that produced it and never again, because the next caller has different
    data. Verification cannot catch that - the locator genuinely is correct today.
    """
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
    """Every strategy worth testing, in the order we would rather rely on them.

    Order is the argument, so it is worth stating. Role and accessible name first,
    because that is what a person reads off the screen and what survives a restyle.
    Then position relative to nearby text, which is the only thing that addresses an
    unlabeled field. Then form attributes, which are stable within one version of a
    product but are the vendor's private naming and can change between releases.
    Visible text last, because it moves around.
    """
    out: list[Strategy] = []

    # The digit rule below applies here too, and for the same reason. It was only
    # written once, on `text`, which was enough while bundles were built for the
    # control an action was about to touch: `forbid` already carried this run's
    # values by then. A checkpoint is built for a control that just *appeared*, and
    # the confirmation panel's link is named after the account number - a number
    # nothing knows yet, because the step that reads it has not run.
    if control.name and not _has_digit(control.name):
        out.append(RoleName(kind="role_name", role=control.role, name=control.name))

    out.extend(_anchored(control, snapshot))

    if control.field_name:
        out.append(FieldName(kind="field_name", name=control.field_name))
    if control.field_id:
        out.append(FieldId(kind="field_id", id=control.field_id))

    # Text containing a digit is almost always data rather than a label - an
    # account number, an amount, a date. Recording it produces a locator that only
    # matches the run it came from, and quietly puts one customer's data into a
    # capability meant to be reused for every customer.
    if control.text and control.text != control.name and not _has_digit(control.text):
        out.append(TextContent(kind="text", text=control.text))

    return out


def _has_digit(text: str) -> bool:
    return any(character.isdigit() for character in text)


def _anchored(control: Control, snapshot: Snapshot) -> list[Strategy]:
    """Anchor to nearby text: first the text above the control, then the text below.

    Above comes first because that is how forms are written - the label sits over the
    field, so "the textbox after the word Username" is the strategy a person would
    describe.

    Below is the fallback, and it is not symmetric decoration. A control that is the
    *last* of its role before the next piece of text cannot be reached from above at
    all: "the link after X" resolves to the first link after X, which is some other
    link. The nav menu's bottom entry hit exactly this and recorded with a single
    strategy. Anchoring from below gives it somewhere to fall.
    """
    out: list[Strategy] = []
    above = sorted(
        (a for a in snapshot.anchors if a.doc_order < control.doc_order),
        key=lambda a: -a.doc_order,
    )[:MAX_ANCHORS]
    below = sorted(
        (a for a in snapshot.anchors if a.doc_order > control.doc_order),
        key=lambda a: a.doc_order,
    )[:MAX_ANCHORS]

    for anchors, position in ((above, "after"), (below, "before")):
        for anchor in anchors:
            # A shortened prefix is offered before the full text, and only survives
            # if it still picks out this one control. The reason is concrete: one
            # real anchor in the target app reads "A minimum of $100.00 must be
            # deposited", and that figure is an administrator setting. Matching the
            # whole sentence works today and breaks the day it changes.
            stable = _prefix_before_first_digit(anchor.text)
            if stable:
                out.append(
                    AnchoredRole(
                        kind="anchored_role",
                        role=control.role,
                        anchor=stable,
                        match="prefix",
                        position=position,  # type: ignore[arg-type]
                    )
                )
            # Never the full text when it carries a digit - only the digit-free
            # prefix above, if there was a usable one. The same rule already applied
            # to a control's own text and it belongs here too. A real recording
            # anchored a checkpoint to "09-11-2026", the day it was made, which
            # matches on exactly one day of the application's life.
            if not _has_digit(anchor.text):
                out.append(
                    AnchoredRole(
                        kind="anchored_role",
                        role=control.role,
                        anchor=anchor.text,
                        position=position,  # type: ignore[arg-type]
                    )
                )
    return out


MIN_PREFIX_CHARS = 8


def _prefix_before_first_digit(text: str) -> str | None:
    """The leading whole words of `text` before the first word containing a digit.

    Whole words, so a currency symbol or a mid-word digit cannot leave a fragment
    behind. None when there is no digit, or when too little text survives - a
    three-character prefix would match half the page. Verification would reject
    that anyway; this just avoids wasting a slot on it.

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
