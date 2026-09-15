// Collect all file IDs and names from Plaud's virtual scroller.
// Run via: safari-browser js "$(cat collect_files.js)"
//
// Plaud uses vue-recycle-scroller which only renders visible items in DOM.
// This script scrolls through the entire list, collecting file info from
// data-testid="file-list-item-{hash}" elements as they render.
//
// Uses .file-list-item__filename selector for clean names (not [class*=name]
// which picks up duration/date metadata).
//
// If no virtual scroller found (all items visible), collects directly.
//
// Output format: one line per file: hash|||filename

new Promise((resolve) => {
  // Try virtual scroller first; if not found, collect directly from DOM
  const scroller =
    document.querySelector(
      ".vue-recycle-scroller.file-list-container__wrapper",
    ) || document.querySelector(".vue-recycle-scroller");

  const files = new Map();

  function collect() {
    document
      .querySelectorAll('[data-testid^="file-list-item-"]')
      .forEach((el) => {
        const id = el
          .getAttribute("data-testid")
          .replace("file-list-item-", "");
        // Use .file-list-item__filename for clean name (avoids duration/date metadata)
        const nameEl = el.querySelector(".file-list-item__filename");
        const name = nameEl ? nameEl.textContent.trim() : "";
        if (id && name.length > 1) files.set(id, name);
      });
  }

  // If no virtual scroller or all items fit in view, collect directly
  if (!scroller) {
    collect();
    const result = [];
    files.forEach((name, id) => result.push(id + "|||" + name));
    resolve(files.size + " files\n" + result.join("\n"));
    return;
  }

  const step = 200;
  const max = scroller.scrollHeight;
  let pos = 0;

  function tick() {
    collect();
    pos += step;
    if (pos <= max + step) {
      scroller.scrollTop = pos;
      setTimeout(tick, 300);
    } else {
      // Scroll back to top
      scroller.scrollTop = 0;
      const result = [];
      files.forEach((name, id) => result.push(id + "|||" + name));
      resolve(files.size + " files\n" + result.join("\n"));
    }
  }

  tick();
});
