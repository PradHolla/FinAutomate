"""Waiting for a declared condition, by watching the screen.

Actions do not guess how long they take. An action acts; the caller then waits for
the condition the artifact declared. That split matters for two reasons.

The practical one: the framework's own idea of "done" is not reliable here. Waiting
for the network to go idle after clicking Submit returns *before* the page updates,
because the request finishes and the script that swaps the panels has not run yet.
Measured against the real app, not assumed.

The design one: polling `observe()` uses nothing browser-specific. A desktop driver
implementing the same six methods gets waiting for free, whereas a wait built on
Playwright's load states would have to be rewritten per surface.
"""

import time

from finautomate.artifact import Checkpoint, ElementVisible, TextVisible
from finautomate.locate import resolve, text_present
from finautomate.surface.models import Snapshot, Surface

POLL_MS = 250


def satisfied(checkpoint: Checkpoint, snapshot: Snapshot) -> bool:
    """Whether a checkpoint holds for one snapshot. Pure - testable with no browser."""
    match checkpoint:
        case ElementVisible():
            return resolve(checkpoint.target, snapshot).found
        case TextVisible():
            return text_present(checkpoint.text, checkpoint.match, snapshot)


def wait_for(surface: Surface, checkpoint: Checkpoint) -> Snapshot | None:
    """Watch until the checkpoint holds. Returns the snapshot that satisfied it, or
    None if it never did within the checkpoint's own timeout.

    The sleep here is a poll interval, not a wait. The condition is what we are
    waiting on; this just decides how often we look.
    """
    deadline = time.monotonic() + checkpoint.timeout_ms / 1000
    while True:
        snapshot = surface.observe()
        if satisfied(checkpoint, snapshot):
            return snapshot
        if time.monotonic() >= deadline:
            return None
        time.sleep(POLL_MS / 1000)
