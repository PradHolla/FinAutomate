(() => {
  // Stale refs from a previous snapshot would let an action target the wrong
  // element after the page has changed underneath it.
  document
    .querySelectorAll("[data-fa-ref]")
    .forEach((el) => el.removeAttribute("data-fa-ref"));

  const normalizeWs = (s) => s.replace(/\s+/g, " ").trim();

  const isVisible = (el) => {
    const style = getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };

  const isInteractive = (el) => {
    const tag = el.tagName;
    if (tag === "INPUT") return el.type !== "hidden";
    if (tag === "SELECT" || tag === "TEXTAREA" || tag === "BUTTON") return true;
    if (tag === "A") return el.hasAttribute("href");
    return false;
  };

  const roleOf = (el) => {
    const tag = el.tagName;
    if (tag === "A") return "link";
    if (tag === "BUTTON") return "button";
    if (tag === "SELECT") return "combobox";
    if (tag === "TEXTAREA") return "textbox";
    if (tag === "INPUT") {
      const type = el.type;
      if (type === "submit" || type === "button" || type === "reset") return "button";
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      if (["text", "password", "email", "tel", "search", "number", "url"].includes(type)) {
        return "textbox";
      }
    }
    return "generic";
  };

  // Priority order is deliberate: an unlabeled ParaBank input must come back ""
  // rather than a name invented from its placeholder, id, or nearby text.
  const nameOf = (el) => {
    const ariaLabel = el.getAttribute("aria-label");
    if (ariaLabel && normalizeWs(ariaLabel)) return normalizeWs(ariaLabel);

    const labelledBy = el.getAttribute("aria-labelledby");
    if (labelledBy) {
      const text = labelledBy
        .split(/\s+/)
        .map((id) => document.getElementById(id))
        .filter(Boolean)
        .map((node) => node.textContent)
        .join(" ");
      if (normalizeWs(text)) return normalizeWs(text);
    }

    if (el.labels && el.labels.length > 0) {
      const text = Array.from(el.labels)
        .map((label) => label.textContent)
        .join(" ");
      if (normalizeWs(text)) return normalizeWs(text);
    }

    const tag = el.tagName;
    if (tag === "INPUT" && ["submit", "button", "reset"].includes(el.type)) {
      if (el.value && normalizeWs(el.value)) return normalizeWs(el.value);
    }

    if (tag === "BUTTON" || tag === "A") {
      const text = normalizeWs(el.textContent);
      if (text) return text;
    }

    const img = el.querySelector("img");
    if (img && img.alt && normalizeWs(img.alt)) return normalizeWs(img.alt);

    const title = el.getAttribute("title");
    if (title && normalizeWs(title)) return normalizeWs(title);

    return "";
  };

  const valueOf = (el) => {
    if (el.tagName === "SELECT") {
      const opt = el.options[el.selectedIndex];
      return opt ? normalizeWs(opt.textContent) : "";
    }
    return "value" in el ? el.value : "";
  };

  const optionsOf = (el) =>
    el.tagName === "SELECT"
      ? Array.from(el.options).map((o) => normalizeWs(o.textContent))
      : [];

  let docOrder = 0;
  let controlIndex = 0;
  const controls = [];
  const anchors = [];

  const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE"]);

  // A block element's own direct text-node children become one anchor; text
  // owned by a descendant element becomes that element's own anchor. That is
  // enough to turn "<p><b>Username</b></p>" into a single anchor without
  // implementing the CSS block-formatting model.
  const collectAnchor = (el) => {
    const text = normalizeWs(
      Array.from(el.childNodes)
        .filter((n) => n.nodeType === Node.TEXT_NODE)
        .map((n) => n.textContent)
        .join(" ")
    );
    if (text.length >= 2 && text.length <= 300) {
      anchors.push({ text, doc_order: docOrder++ });
    }
  };

  const walk = (parent) => {
    for (const child of parent.children) {
      if (SKIP_TAGS.has(child.tagName)) continue;

      if (isInteractive(child)) {
        if (isVisible(child)) {
          const ref = `c${controlIndex++}`;
          child.setAttribute("data-fa-ref", ref);
          controls.push({
            ref,
            role: roleOf(child),
            name: nameOf(child),
            value: valueOf(child),
            field_name: child.getAttribute("name") || "",
            field_id: child.getAttribute("id") || "",
            // A <select>'s textContent is every option run together, which is not
            // a description of the control - it is its data. The options are
            // already reported separately.
            text: child.tagName === "SELECT" ? "" : normalizeWs(child.textContent).slice(0, 200),
            options: optionsOf(child),
            enabled: !child.disabled,
            doc_order: docOrder++,
          });
        }
        // Text inside an interactive control (e.g. a button's own label) is
        // not a separate anchor - do not descend into it.
        continue;
      }

      if (!isVisible(child)) continue;

      collectAnchor(child);
      walk(child);
    }
  };

  // Mid-navigation the old document is gone and the new one has no body yet.
  // An empty snapshot is the honest answer: nothing is on screen. The caller is
  // polling, so it simply looks again.
  if (document.body) walk(document.body);

  return { url: location.href, title: document.title, controls, anchors };
})();
