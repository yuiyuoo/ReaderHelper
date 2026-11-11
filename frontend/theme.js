/* Light/dark theme for every page. Loaded in <head> (not deferred) so the page never flashes the wrong theme.
   Preference: "light" | "dark" | none (= follow the operating system). */
(() => {
  "use strict";
  const KEY = "theme";
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const listeners = [];
  const read = () => { try { return localStorage.getItem(KEY); } catch { return null; } };
  const resolve = () => (read() === "light" || read() === "dark" ? read() : media.matches ? "dark" : "light");

  function apply() {
    const t = resolve();
    document.documentElement.dataset.theme = t;
    document.documentElement.style.colorScheme = t;
    listeners.forEach((fn) => fn(t));
  }

  window.RHTheme = {
    get current() { return resolve(); },
    toggle() {
      try { localStorage.setItem(KEY, resolve() === "dark" ? "light" : "dark"); } catch { /* ignore */ }
      apply();
    },
    onChange(fn) { listeners.push(fn); },
  };
  media.addEventListener("change", apply);
  apply();

  // Any element with [data-theme-toggle] becomes a light/dark switch (icon via CSS).
  document.addEventListener("click", (e) => {
    if (e.target.closest?.("[data-theme-toggle]")) window.RHTheme.toggle();
  });
  const label = () => document.querySelectorAll("[data-theme-toggle]").forEach((b) => {
    const dark = resolve() === "dark";
    b.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
    b.title = b.getAttribute("aria-label");
  });
  listeners.push(label);
  document.addEventListener("DOMContentLoaded", label);
})();
