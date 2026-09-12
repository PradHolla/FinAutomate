"""What a screen looks like from the outside.

This module is the seam: everything above it works on `Snapshot` and never touches
a browser, and everything below it is one driver per kind of surface.
"""

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class Control(BaseModel):
    """One thing on screen a person could act on."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: str
    """Handle for this control, valid only within the snapshot that produced it."""

    role: str
    """ARIA role as the browser computes it. Desktop equivalents map onto the
    same vocabulary."""

    name: str = ""
    """Accessible name as computed by the browser. Empty is common: 42 form
    fields across eight screens in the target app have none."""

    value: str = ""
    field_name: str = ""
    """The form field's `name` attribute. A control's automation id on desktop."""

    field_id: str = ""
    text: str = ""
    options: list[str] = Field(default_factory=list)
    """Visible option labels for comboboxes. Selection is by label, never by
    the underlying value."""

    enabled: bool = True

    doc_order: int
    """Position in reading order. Used instead of pixel geometry because it
    survives a restyle and exists on desktop too."""


class TextAnchor(BaseModel):
    """Visible text that is not itself interactive.

    Makes nameless controls addressable: ParaBank writes `<p><b>Username</b></p>`
    above a bare input, with no markup connecting the two.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    doc_order: int


class Snapshot(BaseModel):
    """Everything we can see right now."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str
    title: str
    controls: list[Control] = Field(default_factory=list)
    anchors: list[TextAnchor] = Field(default_factory=list)

    def control(self, ref: str) -> Control:
        for c in self.controls:
            if c.ref == ref:
                return c
        raise KeyError(f"no control {ref!r} in this snapshot")


class Surface(Protocol):
    """A screen we can look at and act on. Kept to six operations so a second
    implementation stays plausible."""

    def observe(self) -> Snapshot: ...

    def navigate(self, path: str) -> None:
        """Go to a path relative to the surface's configured base."""
        ...

    def click(self, ref: str) -> None: ...

    def type(self, ref: str, text: str) -> None: ...

    def select(self, ref: str, label: str) -> None:
        """Choose an option by its visible label."""
        ...

    def read(self, ref: str) -> str:
        """The control's current visible value or text."""
        ...
