/* One preference for the homepage, project roots, and translated pages. */
(() => {
  "use strict";
  if (window.PyPTOTheme) return;

  const key = "pypto:color-scheme";
  const modes = new Set(["system", "light", "dark"]);
  const system = window.matchMedia("(prefers-color-scheme: dark)");
  function readMode(fallback = "system") {
    try {
      const saved = window.localStorage.getItem(key);
      return modes.has(saved) ? saved : "system";
    } catch {
      // Storage may be unavailable in private or embedded browser contexts.
      return fallback;
    }
  }
  let mode = readMode();

  function apply() {
    const dark = mode === "dark" || (mode === "system" && system.matches);
    document.documentElement.dataset.pyptoTheme = dark ? "dark" : "light";
    document.documentElement.classList.remove("no-js");
    if (document.body) {
      document.body.dataset.mdColorScheme = dark ? "slate" : "default";
    }
    document.querySelectorAll("[data-pypto-theme-select]").forEach(select => {
      select.value = mode;
    });
  }

  function closeProjects(restoreFocus = false) {
    document.querySelectorAll(".pypto-project-switcher[open]").forEach(menu => {
      menu.open = false;
      if (restoreFocus) menu.querySelector("summary").focus();
    });
  }

  function updatePage() {
    apply();
    closeProjects();
    const search = document.querySelector("[data-md-component='search-query']");
    const project = document.querySelector("[data-pypto-project-name]");
    if (search && project) {
      const name = project.dataset.pyptoProjectName;
      const label = document.documentElement.lang.startsWith("zh") ? `搜索 ${name}` : `Search ${name}`;
      search.placeholder = label;
      search.setAttribute("aria-label", label);
    }
  }

  let connected = false;
  window.PyPTOTheme = {
    apply,
    connectMaterial() {
      if (!connected && window.document$) {
        connected = true;
        window.document$.subscribe(updatePage);
      }
      updatePage();
    }
  };

  apply();
  document.addEventListener("DOMContentLoaded", updatePage);
  document.addEventListener("change", event => {
    if (!event.target.matches("[data-pypto-theme-select]")) return;
    const selected = event.target.value;
    if (!modes.has(selected)) return;
    mode = selected;
    try {
      window.localStorage.setItem(key, mode);
    } catch {
      // Keep the selection for this page even when it cannot be persisted.
    }
    apply();
  });
  document.addEventListener("click", event => {
    if (!event.target.closest(".pypto-project-switcher")) closeProjects();
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") closeProjects(true);
  });
  window.addEventListener("storage", event => {
    if (event.key !== key && event.key !== null) return;
    mode = modes.has(event.newValue) ? event.newValue : "system";
    apply();
  });
  window.addEventListener("pageshow", () => {
    mode = readMode(mode);
    apply();
  });
  system.addEventListener("change", () => {
    if (mode === "system") apply();
  });
})();
