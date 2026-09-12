"""Waits for a checkpoint to hold, by polling `observe()` instead of the framework's
own idea of "done" (which returns before this app's panels finish updating).
Polling `observe()` also means a desktop driver inherits waiting for free.
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
    """Poll until the checkpoint holds, or its timeout runs out."""
    deadline = time.monotonic() + checkpoint.timeout_ms / 1000
    while True:
        snapshot = surface.observe()
        if satisfied(checkpoint, snapshot):
            return snapshot
        if time.monotonic() >= deadline:
            return None
        time.sleep(POLL_MS / 1000)
