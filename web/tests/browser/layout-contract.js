// Evaluated in the rendered page by Agent Browser, never by a DOM emulator.
(() => {
  const failures = [];
  const visible = (element) => element.getBoundingClientRect().width > 0;
  const elements = (selector) =>
    [...document.querySelectorAll(selector)].filter(visible);
  const check = (condition, message) => {
    if (!condition) failures.push(message);
  };
  const box = (element) => element.getBoundingClientRect();
  const near = (a, b) => Math.abs(a - b) <= 1;
  const mobile = innerWidth < 768;
  check(
    document.documentElement.scrollWidth <= innerWidth,
    "Page has horizontal overflow",
  );

  for (const control of elements(
    "main .form-control:not(textarea), main .select-control",
  )) {
    const styles = getComputedStyle(control);
    check(
      near(box(control).height, mobile ? 44 : 36),
      `Control height: ${control.id || control.className}`,
    );
    check(
      styles.fontSize === (mobile ? "16px" : "14px"),
      `Control typography: ${control.id || control.className}`,
    );
  }
  for (const button of elements("main .button-action, main .text-action")) {
    const bounds = box(button);
    check(
      bounds.height >= (mobile ? 44 : 36),
      `Action target: ${button.textContent.trim()}`,
    );
    const walker = document.createTreeWalker(button, NodeFilter.SHOW_TEXT);
    const lines = new Set();
    while (walker.nextNode()) {
      if (!walker.currentNode.textContent.trim()) continue;
      const range = document.createRange();
      range.selectNodeContents(walker.currentNode);
      for (const rect of range.getClientRects()) {
        lines.add(Math.round(rect.top));
        check(
          rect.left >= bounds.left - 1 &&
            rect.right <= bounds.right + 1 &&
            rect.top >= bounds.top - 1 &&
            rect.bottom <= bounds.bottom + 1,
          `Action text clipped: ${button.textContent.trim()}`,
        );
      }
    }
    check(lines.size <= 1, `Action text wraps: ${button.textContent.trim()}`);
  }

  const note = document.querySelector(".workflow .page-note");
  if (note) {
    const left = box(note).left;
    for (const element of elements(
      ".step-content h2, .media-browser, .model-profile-field, .submission-bar, .next-action",
    )) {
      check(
        near(box(element).left, left),
        `Workflow alignment: ${element.className || element.tagName}`,
      );
    }
  }
  for (const toolbar of elements(".section-toolbar")) {
    const content = toolbar.querySelector(".toolbar-content");
    const actions = toolbar.querySelector(".toolbar-actions");
    if (!actions || box(actions).top >= box(content).bottom) continue;
    check(
      near(
        box(content).top + box(content).height / 2,
        box(actions).top + box(actions).height / 2,
      ),
      "Toolbar is not vertically centered",
    );
  }
  const listTitle = document.querySelector("#maps-title");
  if (listTitle) {
    for (const name of elements(".term-map-item-name"))
      check(near(box(name).left, box(listTitle).left), "Term map list indentation");
  }
  for (const media of elements(".media-entry-copy")) {
    const row = box(media.closest("button"));
    check(
      box(media).top >= row.top + 4 && box(media).bottom <= row.bottom - 4,
      "Media text has no vertical breathing room",
    );
  }
  const results = document.querySelector(".media-results");
  if (results)
    check(
      box(results).height <= Math.max(480, innerHeight * 0.5) + 1,
      "Media results grow without a bound",
    );
  const active = document.activeElement;
  if (
    active?.matches(".button-control, .form-control, .select-control, .text-action")
  ) {
    const navigation = document.querySelector(".mobile-nav");
    if (navigation && visible(navigation))
      check(
        box(active).bottom <= box(navigation).top + 1,
        "Focused control overlaps mobile navigation",
      );
  }
  return {
    failures,
    width: innerWidth,
    theme: document.documentElement.dataset.theme,
    locale: document.documentElement.lang,
  };
})();
