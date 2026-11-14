/* ReaderHelper – library (home page): book grid, add/edit/remove books, cover pictures. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const grid = $("grid"), status = $("libStatus"), dialog = $("bookDialog");
  let books = [];
  // State of the open dialog. The picture is only uploaded on Save, so Cancel really cancels.
  const dlg = { mode: "edit", book: null, coverFile: null, removeCover: false, previewUrl: null };

  // ---------- rendering ----------

  // Default cover: a calm colour picked from the title, so each book keeps "its" colour.
  const PALETTE = ["#0F6B5C", "#1F4FBF", "#7A3FB0", "#A8480F", "#2F6F3E", "#8A2E4F", "#35556E"];
  const colorFor = (s) => PALETTE[[...s].reduce((h, c) => (h * 31 + c.codePointAt(0)) >>> 0, 7) % PALETTE.length];
  const BOOK_ICON = `<svg width="34" height="34" viewBox="0 0 28 28" fill="none" aria-hidden="true"><path d="M8 9.5c2-.8 4-.8 6 .5v9c-2-1.3-4-1.3-6-.5v-9zM20 9.5c-2-.8-4-.8-6 .5v9c2-1.3 4-1.3 6-.5v-9z" stroke="#fff" stroke-width="1.4" stroke-linejoin="round"/></svg>`;

  function coverInner(title, src) {
    return src
      ? `<img src="${esc(src)}" alt="" loading="lazy">`
      : `${BOOK_ICON}<span class="cover-title">${esc(title)}</span>`;
  }

  function setCover(el, title, src) {
    el.className = src ? "cover" : "cover default";
    el.style.background = src ? "" : colorFor(title || "?");
    el.innerHTML = coverInner(title, src);
  }

  const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  const UNITS = [["year", 31557600], ["month", 2629800], ["week", 604800], ["day", 86400], ["hour", 3600], ["minute", 60]];
  function relTime(iso) {
    const sec = (new Date(iso) - Date.now()) / 1000;
    for (const [unit, size] of UNITS) {
      if (Math.abs(sec) >= size) return rtf.format(Math.round(sec / size), unit);
    }
    return "just now";
  }

  const PENCIL = `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M10.5 2.5l3 3L5 14H2v-3l8.5-8.5z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>`;

  function card(b) {
    // Per person: when *you* last read it and how far (synced across your devices).
    const when = (b.read_at ? `Read ${relTime(b.read_at)}` : "Not started") +
      (b.progress != null ? ` · ${Math.round(b.progress * 100)} %` : "");
    const words = b.word_count ? ` · ${b.word_count} word${b.word_count === 1 ? "" : "s"}` : "";
    return `<article class="book-card">
      <a class="book-open" href="/read?book=${b.id}" aria-label="Read ${esc(b.title)}">
        <div class="cover${b.cover_url ? "" : " default"}" style="${b.cover_url ? "" : `background:${colorFor(b.title)}`}">${coverInner(b.title, b.cover_url)}</div>
      </a>
      <div class="book-meta">
        <div class="book-title" title="${esc(b.title)}">${esc(b.title)}</div>
        ${b.author ? `<div class="book-author">${esc(b.author)}</div>` : ""}
        <div class="book-row">
          ${b.language ? `<span class="lang-tag">${esc(b.language)}</span>` : ""}
          <span class="when" title="${esc(when + words)}">${esc(when + words)}</span>
          <button class="icon-btn" type="button" data-edit="${b.id}" aria-label="Edit ${esc(b.title)}" title="Edit name and picture">${PENCIL}</button>
        </div>
      </div>
    </article>`;
  }

  function render() {
    $("empty").hidden = books.length > 0;
    $("libSub").hidden = books.length === 0;
    grid.innerHTML = books.map(card).join("") + (books.length ? `<div class="book-card">
      <button class="add-card" type="button" data-add>
        <svg width="28" height="28" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M8 3v10M3 8h10" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
        Add book
      </button></div>` : "");
  }

  function setStatus(msg, isError = false) {
    status.textContent = msg;
    status.classList.toggle("error", isError);
  }

  async function load() {
    try {
      books = await (await fetch("/api/books")).json();
      render();
    } catch {
      setStatus("Could not load the library from the server.", true);
    }
    try {
      const n = (await (await fetch("/api/vocab")).json()).length;
      $("vocabCount").textContent = n;
      $("vocabCount").hidden = n === 0;
    } catch { /* the badge is optional */ }
    try {
      const c = await (await fetch("/api/review/counts")).json();
      $("dueCount").textContent = c.due + c.new;
      $("dueCount").hidden = c.due + c.new === 0;
    } catch { /* the badge is optional */ }
  }

  // ---------- adding a book ----------

  async function upload(file) {
    if (!/\.epub$/i.test(file.name)) return setStatus("Please choose an .epub file.", true);
    setStatus(`Adding “${file.name}”…`);
    const fd = new FormData();
    fd.append("file", file);
    try {
      const r = await fetch("/api/books", { method: "POST", body: fd });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Upload failed.");
      setStatus("");
      await load();
      openDialog(data, "new");
    } catch (e) {
      setStatus(e instanceof TypeError ? "Could not reach the ReaderHelper server." : e.message, true);
    }
  }

  $("addBtn").addEventListener("click", () => $("epubInput").click());
  $("epubInput").addEventListener("change", (e) => {
    const f = e.target.files[0];
    e.target.value = "";
    if (f) upload(f);
  });
  grid.addEventListener("click", (e) => {
    if (e.target.closest("[data-add]")) return $("epubInput").click();
    const edit = e.target.closest("[data-edit]");
    if (edit) openDialog(books.find((b) => String(b.id) === edit.dataset.edit), "edit");
  });

  // Drag an EPUB anywhere onto the page.
  let dragDepth = 0;
  const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
  window.addEventListener("dragenter", (e) => { if (hasFiles(e)) { dragDepth++; $("dropHint").hidden = false; } });
  window.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $("dropHint").hidden = true; } });
  window.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
  window.addEventListener("drop", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragDepth = 0;
    $("dropHint").hidden = true;
    const f = [...e.dataTransfer.files].find((x) => /\.epub$/i.test(x.name));
    if (f) upload(f); else setStatus("Only .epub files can be added.", true);
  });

  // ---------- add / edit dialog ----------

  function dlgError(msg) {
    $("dlgError").textContent = msg || "";
    $("dlgError").hidden = !msg;
  }

  function refreshDialogCover() {
    const title = $("fTitle").value.trim() || dlg.book.title;
    const src = dlg.previewUrl || (dlg.removeCover ? null : dlg.book.cover_url);
    setCover($("dlgCover"), title, src);
    $("coverRemove").hidden = !src;
  }

  function openDialog(book, mode) {
    if (!book) return;
    Object.assign(dlg, { mode, book, coverFile: null, removeCover: false });
    if (dlg.previewUrl) URL.revokeObjectURL(dlg.previewUrl);
    dlg.previewUrl = null;
    $("dlgTitle").textContent = mode === "new" ? "Book added – check its name" : "Edit book";
    $("fTitle").value = book.title;
    $("fAuthor").value = book.author || "";
    $("fFile").textContent = `File: ${book.filename}`;
    $("deleteBtn").hidden = mode === "new";
    $("cancelBtn").textContent = mode === "new" ? "Cancel upload" : "Cancel";
    $("saveBtn").textContent = mode === "new" ? "Add to library" : "Save";
    dlgError("");
    refreshDialogCover();
    dialog.showModal();
    $("fTitle").select();
  }

  $("fTitle").addEventListener("input", () => { if (!dlg.previewUrl && (dlg.removeCover || !dlg.book.cover_url)) refreshDialogCover(); });
  $("coverPick").addEventListener("click", () => $("coverInput").click());
  $("coverInput").addEventListener("change", (e) => {
    const f = e.target.files[0];
    e.target.value = "";
    if (!f) return;
    if (!/^image\/(jpeg|png|gif|webp)$/.test(f.type)) return dlgError("Please choose a JPG, PNG, GIF or WebP picture.");
    if (f.size > 5 * 1024 * 1024) return dlgError("The picture is too large (max. 5 MB).");
    dlgError("");
    if (dlg.previewUrl) URL.revokeObjectURL(dlg.previewUrl);
    dlg.coverFile = f;
    dlg.previewUrl = URL.createObjectURL(f);
    dlg.removeCover = false;
    refreshDialogCover();
  });
  $("coverRemove").addEventListener("click", () => {
    if (dlg.previewUrl) URL.revokeObjectURL(dlg.previewUrl);
    Object.assign(dlg, { coverFile: null, previewUrl: null, removeCover: true });
    refreshDialogCover();
  });

  async function api(url, opts) {
    const r = await fetch(url, opts);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Server error (${r.status})`);
    return data;
  }

  $("bookForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const title = $("fTitle").value.trim();
    if (!title) { dlgError("Please enter a name for the book."); return $("fTitle").focus(); }
    $("saveBtn").disabled = true;
    try {
      const id = dlg.book.id;
      await api(`/api/books/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json; charset=utf-8" },
        body: JSON.stringify({ title, author: $("fAuthor").value.trim() }),
      });
      if (dlg.coverFile) {
        const fd = new FormData();
        fd.append("file", dlg.coverFile);
        await api(`/api/books/${id}/cover`, { method: "POST", body: fd });
      } else if (dlg.removeCover && dlg.book.cover) {
        await api(`/api/books/${id}/cover`, { method: "DELETE" });
      }
      dialog.close();
      await load();
    } catch (err) {
      dlgError(err instanceof TypeError ? "Could not reach the ReaderHelper server." : err.message);
    } finally {
      $("saveBtn").disabled = false;
    }
  });

  $("cancelBtn").addEventListener("click", async () => {
    if (dlg.mode === "new") {
      // "Cancel upload": take the just-added book out again.
      try { await api(`/api/books/${dlg.book.id}`, { method: "DELETE" }); } catch { /* still close */ }
      await load();
    }
    dialog.close();
  });

  $("deleteBtn").addEventListener("click", async () => {
    const b = dlg.book;
    if (!confirm(`Remove “${b.title}” from the library?\n\nThe EPUB file is deleted. Your saved words stay.`)) return;
    try {
      await api(`/api/books/${b.id}`, { method: "DELETE" });
      dialog.close();
      await load();
    } catch (err) {
      dlgError(err.message);
    }
  });

  // Esc closes the dialog without changes; a just-uploaded book stays with its detected details.
  dialog.addEventListener("close", () => {
    if (dlg.previewUrl) URL.revokeObjectURL(dlg.previewUrl);
    dlg.previewUrl = null;
  });

  load();
})();
