"""Watching what a person does while they hold the session.

When automation steps aside, the run still has to be auditable. A bank cannot have
a gap in the record that reads "a human did something here". So while the lease is
held by a person, the page reports their clicks and edits back, and they land in the
same evidence log as everything the machine did.

Two things this deliberately does not do. It does not record keystrokes - only that
a field changed, and to what, and never for a password. And it does not interfere:
the listeners are passive and run in the capture phase, so the person's interaction
with the application is exactly what it would be without us watching.
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

  // Never report what was typed into a password field. The point of the audit
  // trail is what happened, not what the credential was.
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
    """Begin recording the person's actions on this page.

    The binding is registered once for the page and survives navigation; the script
    is added both as an init script (for pages loaded from here on) and evaluated
    directly (for the page already on screen).
    """

    def receive(_source: dict[str, Any], action: dict[str, Any]) -> None:
        sink.append(action)

    try:
        page.expose_binding(BINDING, receive)
    except PlaywrightError as err:
        # The only expected failure is re-registering a binding that already exists,
        # which happens if a run hands over twice. Anything else is real.
        if "has been already registered" not in str(err):
            raise
    page.add_init_script(f"({WATCH_JS})()")
    page.evaluate(WATCH_JS)
