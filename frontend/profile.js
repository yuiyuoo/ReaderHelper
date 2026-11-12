/* ReaderHelper – who is reading? Profiles for you and your friends (no passwords: who can reach the app at
   all is decided by Tailscale). Each person has their own saved words and reviews; the library is shared.
   Loaded on every page before the page's own script:
   - remembers the chosen profile on this device,
   - adds it to every request to this server (header X-Profile), and to /api/… download links,
   - shows a profile button in the top bar and a "Who is reading?" dialog. */
(() => {
  "use strict";
  const KEY = "profile";
  const read = () => { try { return JSON.parse(localStorage.getItem(KEY) || "null"); } catch { return null; } };
  const write = (p) => { try { localStorage.setItem(KEY, JSON.stringify(p)); } catch { /* ignore */ } };
  let current = read();  // {id, name} or null

  // Every request to this server carries the profile.
  const origFetch = window.fetch.bind(window);
  window.fetch = (input, init = {}) => {
    const url = typeof input === "string" ? input : input.url;
    if (current && (url.startsWith("/") || url.startsWith(location.origin))) {
      const headers = new Headers(init.headers || (typeof input !== "string" ? input.headers : undefined));
      headers.set("X-Profile", String(current.id));
      init = { ...init, headers };
    }
    return origFetch(input, init);
  };

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function choose(p) {
    write({ id: p.id, name: p.name });
    location.reload();  // simplest way to show this person's words everywhere on the page
  }

  // ---------- dialog ----------

  let dialog = null;
  async function openDialog(forced = false) {
    if (!dialog) {
      dialog = document.createElement("dialog");
      dialog.className = "book-dialog profile-dialog";
      dialog.setAttribute("aria-labelledby", "profileTitle");
      document.body.appendChild(dialog);
      // Without a profile the dialog can't be dismissed with Esc: nothing personal would load.
      dialog.addEventListener("cancel", (e) => { if (!current) e.preventDefault(); });
    }
    let people = [];
    try { people = await (await origFetch("/api/profiles")).json(); } catch { /* shown below */ }
    dialog.innerHTML = `
      <div class="dlg-body profile-body">
        <h2 id="profileTitle">Who is reading?</h2>
        <p class="profile-hint">Everyone has their own saved words and reviews. The books are shared.</p>
        <div class="profile-list">
          ${people.map((p) => `<button type="button" class="profile-pick${current && current.id === p.id ? " current" : ""}"
              data-id="${p.id}" data-name="${esc(p.name)}">
              <span class="avatar">${esc(p.name.slice(0, 1).toUpperCase())}</span>
              <span class="pname">${esc(p.name)}</span><span class="pcount">${p.word_count} words</span></button>`).join("")
            || '<p class="dlg-error">Could not load the profiles.</p>'}
        </div>
        <form class="profile-add" novalidate>
          <label class="field">Add a person<input name="name" maxlength="40" placeholder="Name" autocomplete="off"></label>
          <button type="submit" class="primary">Add</button>
        </form>
        ${current ? `<form class="profile-rename" novalidate>
          <label class="field">Rename “${esc(current.name)}”<input name="name" maxlength="40" value="${esc(current.name)}" autocomplete="off"></label>
          <button type="submit">Rename</button></form>` : ""}
        <p class="dlg-error" role="alert" hidden></p>
      </div>
      ${current && !forced ? '<div class="dlg-actions"><span class="spacer"></span><button type="button" data-close>Close</button></div>' : ""}`;
    const err = dialog.querySelector(".dlg-error[role=alert]");
    const fail = (msg) => { err.textContent = msg; err.hidden = false; };
    dialog.onclick = (e) => {
      const pick = e.target.closest(".profile-pick");
      if (pick) choose({ id: Number(pick.dataset.id), name: pick.dataset.name });
      if (e.target.closest("[data-close]")) dialog.close();
    };
    dialog.querySelector(".profile-add").onsubmit = async (e) => {
      e.preventDefault();
      const name = e.target.name.value.trim();
      if (!name) return fail("Please enter a name.");
      const r = await origFetch("/api/profiles", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) return fail(typeof data.detail === "string" ? data.detail : "Could not add the person.");
      choose(data);
    };
    const rename = dialog.querySelector(".profile-rename");
    if (rename) rename.onsubmit = async (e) => {
      e.preventDefault();
      const name = e.target.name.value.trim();
      if (!name) return fail("Please enter a name.");
      const r = await origFetch(`/api/profiles/${current.id}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) return fail(typeof data.detail === "string" ? data.detail : "Could not rename.");
      choose(data);
    };
    if (!dialog.open) dialog.showModal();
  }

  // ---------- top-bar button ----------

  function addButton() {
    const bar = document.querySelector(".read-bar .read-right") || document.querySelector(".toolbar");
    if (!bar || !current) return;
    const b = document.createElement("button");
    b.type = "button";
    b.className = "profile-chip";
    b.title = `Reading as ${current.name} – click to switch`;
    b.setAttribute("aria-label", b.title);
    b.innerHTML = `<span class="avatar">${esc(current.name.slice(0, 1).toUpperCase())}</span><span class="pname">${esc(current.name)}</span>`;
    b.addEventListener("click", () => openDialog());
    bar.appendChild(b);
  }

  /** Download links to /api/… (CSV export) can't send a header, so they get ?profile=. */
  function tagLinks() {
    if (!current) return;
    document.querySelectorAll('a[href^="/api/"]').forEach((a) => {
      const u = new URL(a.getAttribute("href"), location.origin);
      u.searchParams.set("profile", current.id);
      a.setAttribute("href", u.pathname + u.search);
    });
  }

  document.addEventListener("DOMContentLoaded", async () => {
    // A remembered profile that no longer exists (e.g. another database) counts as none.
    if (current) {
      try {
        const people = await (await origFetch("/api/profiles")).json();
        const me = people.find((p) => p.id === current.id);
        if (!me) { current = null; try { localStorage.removeItem(KEY); } catch { /* ignore */ } }
        else if (me.name !== current.name) { current = { id: me.id, name: me.name }; write(current); }
      } catch { /* offline: keep it */ }
    }
    addButton();
    tagLinks();
    if (!current) openDialog(true);
  });

  window.RHProfile = { get current() { return current; }, open: () => openDialog() };
})();
