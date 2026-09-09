"""The browser driver: fills `Snapshot` from a live Playwright page.

This module owns the DOM. Nothing above `models.py` should ever need to know an
element attribute is called `data-fa-ref` - that is this file's private
implementation of "ref", not part of the `Surface` contract.
"""

from pathlib import Path

from playwright.sync_api import Locator, Page

from finautomate.surface.models import Snapshot

_EXTRACT_JS = (Path(__file__).parent / "extract.js").read_text(encoding="utf-8")

# Kept small and duplicated rather than shared with extract.js: `read` only ever
# needs the current value or text of one already-located element, not a full
# re-walk of the page.
_VALUE_JS = """el => {
    if (el.tagName === 'SELECT') {
        const opt = el.options[el.selectedIndex];
        return opt ? opt.textContent.trim() : '';
    }
    return 'value' in el ? el.value : '';
}"""
_TEXT_JS = "el => (el.textContent || '').trim()"
_OPTION_LABELS_JS = "el => Array.from(el.options).map(o => o.textContent.trim())"


class ControlNotFoundError(Exception):
    """Raised when a ref does not resolve to any element on the current page."""

    def __init__(self, ref: str) -> None:
        super().__init__(f"no control with ref {ref!r} on the current page")
        self.ref = ref


class OptionNotFoundError(Exception):
    """Raised when a select's options do not include the requested label.

    Distinct from `ControlNotFoundError` on purpose: this means the dropdown is
    there but the app is not offering that choice, which callers need to treat
    as a business outcome rather than a broken locator.
    """

    def __init__(self, ref: str, label: str, available_labels: list[str]) -> None:
        super().__init__(
            f"option {label!r} not found for control {ref!r}; available: {available_labels}"
        )
        self.ref = ref
        self.label = label
        self.available_labels = available_labels


class BrowserSurface:
    """Drives one Playwright page. Does not launch or own the browser."""

    def __init__(self, page: Page, base_url: str) -> None:
        self._page = page
        self._base_url = base_url

    def observe(self) -> Snapshot:
        # Only that a document exists and is parsed - deliberately not "the app has
        # finished working", which is not something the browser can tell us.
        self._page.wait_for_load_state("domcontentloaded")
        data: dict[str, object] = self._page.evaluate(_EXTRACT_JS)
        return Snapshot.model_validate(data)

    def navigate(self, path: str) -> None:
        if "://" in path or path.startswith("//"):
            raise ValueError(f"navigate takes a relative path, got {path!r}")
        url = f"{self._base_url.rstrip('/')}/{path.lstrip('/')}"
        self._page.goto(url)

    def click(self, ref: str) -> None:
        self._locate(ref).click()

    def type(self, ref: str, text: str) -> None:
        self._locate(ref).fill(text)

    def select(self, ref: str, label: str) -> None:
        locator = self._locate(ref)
        available: list[str] = locator.evaluate(_OPTION_LABELS_JS)
        if label not in available:
            raise OptionNotFoundError(ref, label, available)
        locator.select_option(label=label)

    def read(self, ref: str) -> str:
        locator = self._locate(ref)
        value: str = locator.evaluate(_VALUE_JS)
        if value:
            return value
        text: str = locator.evaluate(_TEXT_JS)
        return text

    def _locate(self, ref: str) -> Locator:
        locator = self._page.locator(f'[data-fa-ref="{ref}"]')
        if locator.count() == 0:
            raise ControlNotFoundError(ref)
        return locator

    # There is deliberately no settle-after-action here. Waiting for the network to
    # go idle looks right and is not: measured against the real app, it returns
    # before the script that swaps the panels has run, so the caller sees the old
    # page. Actions act; `checkpoint.wait_for` waits for the condition the artifact
    # declared. That also keeps every browser-specific wait primitive out of the
    # Surface contract, so a desktop driver inherits the same waiting logic.
