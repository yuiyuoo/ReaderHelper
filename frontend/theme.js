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

/** Grammar line ("der Schlüssel, die Schlüssel" / "fängt an · fing an · hat angefangen") as HTML, with the
    article coloured by gender: der blue, die red, das green, plural-only grey. Used by reader, cards, list. */
window.grammarHtml = (text, gender) => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  if (!text) return "";
  const m = /^(der|die|das) /.exec(text);
  if (!m) return esc(text);
  const g = gender || (text.includes("(nur Plural)") ? "pl" : { der: "m", die: "f", das: "n" }[m[1]]);
  return `<span class="art g-${g}">${m[1]}</span> ${esc(text.slice(m[0].length))}`;
};
