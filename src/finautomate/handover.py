"""Watching what a person does while they hold the session.

While the lease is held by a person, the page reports their clicks and field
changes back, so the run stays auditable with no gap in the record. It never
records a password, and the listeners are passive so they don't change how the
page behaves.
"""

from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

BINDING = "__faRecordHumanAction"

WATCH_JS = """
() => {
  if (window.__faWatching) return;
  window.__faWatching = true;

  const describe = (el) => {
    if (!el || !el.tagName) return "unknown";
    const label =
      el.getAttribute("aria-label") ||
      (el.labels && el.labels[0] && el.labels[0].innerText) ||
      (el.tagName === "INPUT" && ["submit", "button"].includes(el.type) ? el.value : "") ||
      (el.tagName === "A" || el.tagName === "BUTTON" ? el.textContent : "") ||
      el.getAttribute("name") ||
      el.id ||
      "";
    const tag = el.tagName.toLowerCase();
    const clean = (label || "").replace(/\\s+/g, " ").trim().slice(0, 80);
    return clean ? `${tag} "${clean}"` : tag;
  };

  // Never report what was typed into a password field.
  const valueOf = (el) => {
    if (!el || el.type === "password") return "<redacted>";
    if (el.tagName === "SELECT") {
      const opt = el.options[el.selectedIndex];
      return opt ? opt.textContent.trim() : "";
    }
    return (el.value || "").slice(0, 80);
  };

  document.addEventListener(
    "click",
    (e) => window.__faRecordHumanAction({ kind: "click", target: describe(e.target), value: "" }),
    true
  );
  document.addEventListener(
    "change",
    (e) =>
      window.__faRecordHumanAction({
        kind: "change",
        target: describe(e.target),
        value: valueOf(e.target),
      }),
    true
  );
}
"""


def start_watching(page: Page, sink: list[dict[str, Any]]) -> None:
    """Start recording the person's actions on this page. Adds the script as an
    init script for future navigations, and evaluates it now for the current page.
    """

    def receive(_source: dict[str, Any], action: dict[str, Any]) -> None:
        sink.append(action)

    try:
        page.expose_binding(BINDING, receive)
    except PlaywrightError as err:
        # Only expected failure: re-registering a binding, if a run hands over twice.
        if "has been already registered" not in str(err):
            raise
    page.add_init_script(f"({WATCH_JS})()")
    page.evaluate(WATCH_JS)
