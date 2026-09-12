"""Writing down what happened, in a form someone can audit afterwards.

One run, one directory: a JSONL log of decisions, the raw model transcript kept
separate from it, and screenshots when something goes wrong.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REDACTED = "<redacted>"


class Evidence:
    """A run directory. Everything written through here is already redacted."""

    def __init__(self, root: Path, run_id: str, secrets: frozenset[str] = frozenset()) -> None:
        self.dir = root / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self._secrets = frozenset(s for s in secrets if s)
        self._log = self.dir / "run.jsonl"

    def event(self, kind: str, **fields: Any) -> None:
        record = {
            "at": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "kind": kind,
            **fields,
        }
        line = self._redact(json.dumps(record, default=str))
        with self._log.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def transcript(self, messages: list[Any]) -> None:
        """The raw model conversation, kept out of the artifact by design."""
        text = self._redact(json.dumps(messages, indent=2, default=str))
        (self.dir / "transcript.json").write_text(text, encoding="utf-8")

    def screenshot(self, page: Any, label: str) -> Path:
        """Named after what failed, not `error.png`. Ten failures, ten filenames."""
        path = self.dir / f"{label}.png"
        page.screenshot(path=str(path), full_page=True)
        return path

    def _redact(self, text: str) -> str:
        """The one place everything passes through, so nothing forgets to redact."""
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        return text
