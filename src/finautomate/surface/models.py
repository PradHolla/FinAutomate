"""What a screen looks like from the outside.

This module is the seam. Everything above it - locator resolution, replay, the
discovery loop - works on `Snapshot` and never touches a browser. Everything below
it is one driver per kind of surface.

A `Snapshot` is deliberately flat and boring: a list of controls and a list of text
anchors, each with a role, a name, and a position. That shape is not web-specific.
Windows UI Automation and macOS Accessibility both expose a control tree with roles
and names, so a desktop driver fills in the same structure from a different source
and nothing above this line changes.
"""

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class Control(BaseModel):
    """One thing on screen a person could act on."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: str
    """Handle for acting on this control. Valid only within the snapshot that
    produced it - take a new snapshot after the page changes."""

    role: str
    """ARIA role as the browser computes it: button, link, textbox, combobox,
    checkbox, radio. The desktop equivalents map onto the same vocabulary."""

    name: str = ""
    """Accessible name. Empty is common and expected - none of ParaBank's 36 input
    fields has one, which is the entire reason the locator ladder exists."""

    value: str = ""
    field_name: str = ""
    """The form field's `name` attribute. A control's automation id on desktop."""

    field_id: str = ""
    text: str = ""
    options: list[str] = Field(default_factory=list)
    """Visible option labels, for comboboxes. We select by label, never by the
    underlying option value."""

    enabled: bool = True

    doc_order: int
    """Position in reading order. Anchoring works off this rather than pixel
    geometry, because reading order survives a restyle and exists on desktop too."""


class TextAnchor(BaseModel):
    """Visible text that is not itself interactive.

    These are what make nameless controls addressable. ParaBank writes
    `<p><b>Username</b></p>` above a bare input, so "Username" is an anchor even
    though no markup connects it to the field.
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
    """A screen we can look at and act on.

    Six operations. Keeping this small is what makes a second implementation
    plausible rather than theoretical.
    """

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
