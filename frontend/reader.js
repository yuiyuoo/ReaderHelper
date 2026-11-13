/* ReaderHelper – EPUB reader with click-to-lookup. Vanilla JS, no build step. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const els = {
    bookTitle: $("bookTitle"), tocBtn: $("tocBtn"), tocMenu: $("tocMenu"), tocList: $("tocList"),
    chapter: $("chapterLabel"), pct: $("progressPct"), fill: $("progressFill"),
    fontDown: $("fontDown"), fontUp: $("fontUp"), targetGroup: $("targetGroup"), spreadBtn: $("spreadBtn"), markBtn: $("markBtn"),
    viewer: $("viewer"), prev: $("prev"), next: $("next"), vocabCount: $("vocabCount"),
    status: $("status"), popover: $("popover"), autoplay: $("autoplay"), selBtn: $("selBtn"),
  };
  // Popover labels in the chosen meaning language.
  const LABELS = {
    vi: { tr: "Tiếng Việt", gloss: "Nghĩa trong câu này", passage: "Bản dịch", notes: "Ghi chú" },
    en: { tr: "English", gloss: "Meaning in this sentence", passage: "Translation", notes: "Notes" },
    de: { tr: "Deutsch", gloss: "Bedeutung in diesem Satz", passage: "Übersetzung", notes: "Hinweise" },
  };
  const MAX_PASSAGE_CHARS = 600;  // keep in sync with backend/main.py
  const MAX_PHRASE_WORDS = 4;     // longer selections are translated as a passage
  const FONT_MIN = 70, FONT_MAX = 170, FONT_STEP = 10;  // text size in %
  let pendingSel = null;          // selection waiting for the Translate button
  let addingTo = null;            // popup state waiting for "+ Add word" (the next tapped word)

  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* ignore */ } },
  };

  let book = null, rendition = null, bookName = "", bookLang = "";
  let inflight = null;          // AbortController of the current /lookup
  let lookupSeq = 0;            // guards against late responses
  let savedWords = new Set();   // lowercase spellings of saved words (fallback highlighting)
  let savedPhrases = new Set(); // saved entries of several words, highlighted by spelling
  let currentTarget = LABELS[store.get("target")] ? store.get("target") : "vi";
  let autoplay = store.get("autoplay") === "1";
  let fontPct = Math.min(FONT_MAX, Math.max(FONT_MIN, Number(store.get("fontSize")) || 100));
  let twoPages = store.get("spread") === "2";  // two pages side by side, like an open book
  const MIN_SPREAD_WIDTH = 760;  // below this width epub.js falls back to one page by itself
  let tocItems = [];            // flattened TOC: {label, href, depth, spineIndex}
  let currentBookId = null;     // library id of the open book (saved words remember it)
  let markSaved = store.get("markSaved") !== "0";  // highlight saved words in the text
  // "Open in book" from the vocabulary/review pages: /read?book=<id>&cfi=<position of the word>
  const jumpCfi = new URLSearchParams(location.search).get("cfi");
  // Last position reported by epub.js. Its currentLocation() can return a stale page
  // (e.g. after the web font changed the line breaks), so this is used instead.
  let lastLocation = null;
  // Text the reader was at when switching one/two pages. Kept until they turn a page themselves, so
  // switching back and forth always returns to the same page (a spread starts one page earlier).
  let spreadAnchor = null;
  const target = () => currentTarget;

  // ---------- book (from /read?book=<id>) ----------

  async function loadBookFromUrl() {
    const id = new URLSearchParams(location.search).get("book");
    if (!id) { location.replace("/"); return; }
    try {
      const r = await fetch(`/api/books/${encodeURIComponent(id)}`);
      if (r.status === 404) {
        els.viewer.innerHTML = `<p class="placeholder">This book is no longer in your library. <a href="/">Back to the library</a></p>`;
        els.chapter.textContent = "Book not found";
        return;
      }
      const info = await r.json();
      els.bookTitle.textContent = info.title;
      currentBookId = info.id;
      document.title = `${info.title} · ReaderHelper`;
      fetch(`/api/books/${info.id}/opened`, { method: "POST" }).catch(() => {});
      openBook(info);
    } catch {
      setStatus("Could not reach the ReaderHelper server.");
    }
  }

  async function openBook(info) {
    closePopover();
    // Positions are stored per file name, so reading positions from before the library still work.
    bookName = info.filename;
    els.viewer.innerHTML = "";  // remove the "Opening book…" placeholder, epub.js appends to the container

    // Load the language model now, so the first word lookup doesn't also wait for that.
    // Preload only spaCy: words are looked up without the AI unless "Ask AI" is pressed, so the
    // language model (~6 GB) is loaded on first use instead.
    fetch("/api/warmup?ai=false", { method: "POST" }).catch(() => {});
    book = ePub(info.file_url, { openAs: "epub" });
    book.spine.hooks.serialize.register(disarmScripts);
    createRendition();

    try {
      await book.ready;
      bookLang = (book.packaging.metadata.language || "").slice(0, 2).toLowerCase();
      await displayAt(jumpCfi || store.get(`pos:${bookName}`));
      if (jumpCfi) {
        flashWord(jumpCfi);
        // A later reload should continue where you read, not jump to the word again.
        history.replaceState(null, "", `/read?book=${encodeURIComponent(currentBookId)}`);
      }
      els.prev.disabled = els.next.disabled = false;
      await buildToc();
      updateChapter(lastLocation);
      setupProgress();  // may take a moment for long books; runs after the page is shown
    } catch (e) {
      console.error(e);
      els.chapter.textContent = "Could not open the book";
      setStatus(`Could not open this EPUB: ${e.message || e}`);
    }
  }

  /** Show `cfi`, then again once the book's web font has loaded: the font changes line breaks,
   *  which would otherwise leave the view on a different page than the one asked for. */
  async function displayAt(cfi) {
    await rendition.display(cfi || undefined);
    if (!cfi) return;
    await Promise.all(rendition.getContents().map((c) => c.rhFontReady).filter(Boolean));
    await rendition.display(cfi);
    // A saved position is the first character of a page, i.e. exactly on a page boundary. epub.js sometimes
    // rounds that down and shows the page before, so turn forward until the position is really on screen.
    for (let i = 0; i < 3 && !isOnScreen(cfi); i++) await rendition.next();
  }

  function isOnScreen(cfi) {
    let range;
    try { range = rendition.getRange(cfi); } catch { return true; }  // can't check: accept
    if (!range) return true;
    // A saved position is an empty range, which has no on-screen box: measure the first character instead.
    const r0 = range.cloneRange();
    const n = r0.startContainer;
    if (n.nodeType === Node.TEXT_NODE && r0.startOffset < n.length) r0.setEnd(n, r0.startOffset + 1);
    else if (n.nodeType === Node.ELEMENT_NODE) r0.selectNodeContents(n.childNodes[r0.startOffset] || n);
    const r = r0.getClientRects()[0];
    if (!r) return true;
    const frame = n.ownerDocument.defaultView.frameElement.getBoundingClientRect();
    const view = els.viewer.getBoundingClientRect();
    const x = frame.left + r.left + r.width / 2;
    return x >= view.left - 1 && x <= view.right + 1;
  }

  /** epub.js hook, runs on each chapter's HTML before it is shown: book scripts can't run.
   *  <script> elements are kept but made inert (type="text/plain"), so the element count and therefore
   *  saved positions (CFIs) don't change; on…="" event attributes are renamed. */
  function disarmScripts(output, section) {
    section.output = output
      .replace(/<script\b[^>]*\/>/gi, '<script type="text/plain"/>')
      .replace(/<script\b(?:[^>]*[^>/])?>[\s\S]*?<\/script\s*>/gi, '<script type="text/plain"></script>')
      .replace(/(<[^>]*?\s)on([a-z]+)(\s*=)/gi, "$1data-blocked-on$2$3");
  }

  /** The view of the book (one or two pages). Rebuilt when switching page mode. */
  function createRendition() {
    rendition = book.renderTo(els.viewer, {
      width: "100%", height: "100%", flow: "paginated",
      spread: twoPages ? "always" : "none", minSpreadWidth: MIN_SPREAD_WIDTH,
      // Without this the book's iframe gets sandbox="allow-same-origin" only. Chrome still delivers taps
      // and clicks inside it to our listeners, but Safari/WebKit (every browser on iPhone/iPad) delivers
      // no events at all, so tapping words did nothing there. The book's own scripts are neutralised
      // by disarmScripts() below, so this doesn't let book content run code.
      allowScriptedContent: true,
    });
    rendition.hooks.content.register(onContent);
    rendition.on("relocated", onRelocated);
    rendition.on("keyup", onKey);
    rendition.themes.fontSize(`${fontPct}%`);
  }

  // ---------- table of contents (chapter menu) ----------

  async function buildToc() {
    const nav = await book.loaded.navigation;
    tocItems = [];
    const add = (items, depth) => items.forEach((it) => {
      // The spine index tells which file (section) a TOC entry points into.
      const section = book.spine.get(it.href.split("#")[0]) || book.spine.get(it.href);
      tocItems.push({ label: it.label.trim(), href: it.href, depth, spineIndex: section ? section.index : -1 });
      if (it.subitems?.length) add(it.subitems, depth + 1);
    });
    add(nav.toc, 0);
    els.tocList.innerHTML = tocItems.length
      ? tocItems.map((t, i) => `<button type="button" class="toc-item" data-i="${i}" style="padding-left:${14 + t.depth * 16}px">${esc(t.label)}</button>`).join("")
      : `<p class="toc-empty">This book has no table of contents.</p>`;
  }

  /** The chapter shown on this page: the last TOC entry that starts before the end of the page.
   *  Several chapters can share one file (separated by #anchors), so anchors are compared by position. */
  function chapterAt(loc) {
    const idx = loc?.start?.index;
    if (idx == null) return null;
    let found = null;
    for (const t of tocItems) {  // best guess from earlier files
      if (t.spineIndex >= 0 && t.spineIndex < idx && (!found || t.spineIndex >= found.spineIndex)) found = t;
    }
    const doc = rendition.getContents().find((c) => c.sectionIndex === idx)?.document;
    let pageEnd = null;
    try { pageEnd = rendition.getRange(loc.end.cfi); } catch { /* fall back to "whole file" */ }
    for (const t of tocItems) {
      if (t.spineIndex !== idx) continue;
      const frag = t.href.split("#")[1];
      const el = frag && doc ? doc.getElementById(decodeURIComponent(frag)) : null;
      if (!frag || !el || !pageEnd) { found = t; continue; }  // starts at the top of the file
      const before = el.contains(pageEnd.startContainer) ||
        (el.compareDocumentPosition(pageEnd.startContainer) & Node.DOCUMENT_POSITION_FOLLOWING);
      if (before) found = t; else break;
    }
    return found;
  }

  function updateChapter(loc) {
    const ch = chapterAt(loc);
    const total = book?.spine?.length || 0;
    els.chapter.textContent = ch ? ch.label : loc?.start ? `Section ${loc.start.index + 1} of ${total}` : "";
    els.tocBtn.title = els.chapter.textContent;
    $("footChapter").textContent = els.chapter.textContent;
    els.tocList.querySelectorAll(".toc-item").forEach((b) => {
      const on = ch && tocItems[b.dataset.i] === ch;
      b.classList.toggle("current", !!on);
      if (on) b.setAttribute("aria-current", "true"); else b.removeAttribute("aria-current");
    });
  }

  function setTocOpen(open) {
    els.tocMenu.hidden = !open;
    els.tocBtn.setAttribute("aria-expanded", String(open));
    if (open) (els.tocList.querySelector(".current") || els.tocList.querySelector("button"))?.focus();
  }
  els.tocBtn.addEventListener("click", () => setTocOpen(els.tocMenu.hidden));
  els.tocList.addEventListener("click", (e) => {
    const b = e.target.closest(".toc-item");
    if (!b) return;
    setTocOpen(false);
    spreadAnchor = null;
    rendition?.display(tocItems[b.dataset.i].href);
  });
  // pointerdown, not mousedown: iPhone Safari sends no mouse events for taps on plain areas.
  document.addEventListener("pointerdown", (e) => {
    if (!els.tocMenu.hidden && !e.target.closest(".toc-wrap")) setTocOpen(false);
  });

  // ---------- progress ----------

  /** Reading progress needs "locations" (positions spread through the book). Generating them reads the
   *  whole book once, so they are cached per book in localStorage. */
  async function setupProgress() {
    const key = `loc:${bookName}`;
    const saved = store.get(key);
    try {
      if (saved) book.locations.load(saved);
      if (!book.locations.length()) {
        await book.locations.generate(1200);
        store.set(key, book.locations.save());
      }
    } catch (e) {
      console.warn("progress unavailable", e);
    }
    updateProgress(lastLocation);
  }

  function updateProgress(loc) {
    if (!loc?.start || !book?.locations?.length()) return;
    const p = loc.atEnd ? 1 : book.locations.percentageFromCfi(loc.start.cfi);
    const pct = Math.round(p * 100);
    els.pct.textContent = `${pct}%`;
    els.fill.style.width = `${pct}%`;
    $("footPct").textContent = `${pct}%`;
  }

  function onRelocated(loc) {
    lastLocation = loc;
    hideSelBtn();
    store.set(`pos:${bookName}`, loc.start.cfi);
    updateChapter(loc);
    updateProgress(loc);
  }

  // ---------- controls ----------

  // Turning pages yourself ends the "same text as before switching one/two pages" memory.
  const goPrev = () => { spreadAnchor = null; setBars(false); rendition?.prev(); };
  const goNext = () => { spreadAnchor = null; setBars(false); rendition?.next(); };

  // ---------- focus mode (phones): only the book on screen ----------
  // The top bar lies over the page instead of taking room, so showing/hiding it never changes the page
  // size (that would re-flow the book). Turning a page hides it; tapping the margin around the text or the
  // bottom line brings it back. Taps on the text itself stay word lookups.
  function setBars(show) {
    if (!PHONE.matches) show = true;
    if (!show) setTocOpen(false);
    document.body.classList.toggle("bars-hidden", !show);
  }
  const toggleBars = () => setBars(document.body.classList.contains("bars-hidden"));
  document.querySelector(".read-stage").addEventListener("click", (e) => {
    if (PHONE.matches && !e.target.closest(".page-nav") && Date.now() - closedPopoverAt > 700) toggleBars();
  });
  $("readFoot").addEventListener("click", toggleBars);
  els.prev.addEventListener("click", goPrev);
  els.next.addEventListener("click", goNext);

  function onKey(e) {
    if (e.key === "Escape") { setTocOpen(false); return closePopover(); }
    if (!rendition || ["SELECT", "INPUT", "TEXTAREA"].includes(e.target?.tagName)) return;
    if (!els.tocMenu.hidden) return;
    if (e.key === "ArrowLeft") goPrev();
    if (e.key === "ArrowRight") goNext();
  }
  document.addEventListener("keyup", onKey);

  function setFont(pct) {
    fontPct = Math.min(FONT_MAX, Math.max(FONT_MIN, pct));
    store.set("fontSize", fontPct);
    els.fontDown.disabled = fontPct <= FONT_MIN;
    els.fontUp.disabled = fontPct >= FONT_MAX;
    rendition?.themes.fontSize(`${fontPct}%`);
  }
  els.fontDown.addEventListener("click", () => setFont(fontPct - FONT_STEP));
  els.fontUp.addEventListener("click", () => setFont(fontPct + FONT_STEP));

  function renderTarget() {
    els.targetGroup.querySelectorAll("[data-lang]").forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.lang === currentTarget)));
  }
  els.targetGroup.addEventListener("click", (e) => {
    const b = e.target.closest("[data-lang]");
    if (!b) return;
    currentTarget = b.dataset.lang;
    store.set("target", currentTarget);
    renderTarget();
    closePopover();
  });

  function renderSpread() {
    document.body.classList.toggle("two-pages", twoPages);
    els.spreadBtn.setAttribute("aria-pressed", String(twoPages));
  }
  els.spreadBtn.addEventListener("click", async () => {
    twoPages = !twoPages;
    store.set("spread", twoPages ? "2" : "1");
    renderSpread();  // the page card gets wider/narrower via CSS
    if (!rendition) return;
    // Re-layouting in place makes epub.js jump to the start of the section (and save that position),
    // so the view is rebuilt and opened at the text that was on screen.
    const cfi = spreadAnchor || lastLocation?.start?.cfi || store.get(`pos:${bookName}`);
    spreadAnchor = cfi;
    closePopover();
    hideSelBtn();
    rendition.destroy();
    els.viewer.innerHTML = "";
    createRendition();
    await displayAt(cfi);
  });

  function renderMark() { els.markBtn.setAttribute("aria-pressed", String(markSaved)); }
  els.markBtn.addEventListener("click", () => {
    markSaved = !markSaved;
    store.set("markSaved", markSaved ? "1" : "0");
    renderMark();
    rendition?.getContents().forEach(highlightSaved);
  });

  function renderAutoplay() { els.autoplay.setAttribute("aria-pressed", String(autoplay)); }
  els.autoplay.addEventListener("click", () => {
    autoplay = !autoplay;
    store.set("autoplay", autoplay ? "1" : "0");
    renderAutoplay();
  });

  let statusTimer = null;
  function setStatus(msg) {
    els.status.textContent = msg;
    els.status.hidden = !msg;
    clearTimeout(statusTimer);
    if (msg) statusTimer = setTimeout(() => { els.status.hidden = true; }, 6000);
  }

  // ---------- book typography + light/dark inside the book (iframe) ----------

  const BOOK_FONT_URL = "https://fonts.googleapis.com/css2?family=Literata:ital,opsz,wght@0,7..72,400;0,7..72,600;1,7..72,400&subset=vietnamese&display=swap";
  // Our own stylesheet (instead of epub.js themes, which leave old theme rules in place when switching).
  const BOOK_CSS = `
    html { --rh-text:#22251f; --rh-link:#2d6a4f; --rh-hl:#dbeadb; --rh-ul:#2d6a4f; --rh-sel:#cfe3d4; }
    html.rh-dark { --rh-text:#e7e3d9; --rh-link:#82c29e; --rh-hl:#2f4a39; --rh-ul:#82c29e; --rh-sel:#35503f; }
    /* The iframe's colour scheme must match the page, or the browser paints an opaque white backdrop. */
    html { background: transparent !important; color-scheme: light; }
    /* Swipes turn pages; without this the browser treats a right swipe as "go back". */
    html, body { overscroll-behavior-x: none; }
    html.rh-dark { color-scheme: dark; }
    body { font-family: "Literata", Georgia, serif !important; color: var(--rh-text) !important;
           background: transparent !important; line-height: 1.7 !important; cursor: default;
           -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility; }
    p, li, blockquote, dd, dt, td, th, div, span, h1, h2, h3, h4, h5, h6 { font-family: inherit !important; }
    h1, h2, h3, h4 { line-height: 1.25 !important; font-weight: 600 !important; letter-spacing: -0.01em; }
    a { color: var(--rh-link) !important; }
    ::selection { background: var(--rh-sel); }
    ::highlight(jump-word) { background-color: #f6d36b; color: #22251f; }
    ::highlight(saved-word) { background-color: var(--rh-hl); text-decoration: underline;
                              text-decoration-color: var(--rh-ul); text-underline-offset: 3px; }
    /* Dark: books often hard-code black text or white boxes, so neutralise them. */
    html.rh-dark body * { color: inherit !important; background-color: transparent !important; }
    html.rh-dark hr { border-color: #3a3d36 !important; }
    html.rh-dark img { opacity: .92; }
  `;

  function styleContents(contents) {
    const doc = contents.document;
    if (!doc.getElementById("rh-book-css")) {
      const link = doc.createElement("link");
      link.rel = "stylesheet";
      link.href = BOOK_FONT_URL;
      // Resolves once Literata is really usable (or after 3 s / on error), see displayAt().
      contents.rhFontReady = new Promise((resolve) => {
        link.onload = () => Promise.all(["400 1em Literata", "italic 400 1em Literata", "600 1em Literata"]
          .map((f) => doc.fonts.load(f))).then(resolve, resolve);
        link.onerror = resolve;
        setTimeout(resolve, 3000);
      });
      doc.head.appendChild(link);
      const style = doc.createElement("style");
      style.id = "rh-book-css";
      style.textContent = BOOK_CSS;
      doc.head.appendChild(style);
    }
    doc.documentElement.classList.toggle("rh-dark", window.RHTheme?.current === "dark");
  }
  window.RHTheme?.onChange(() => rendition?.getContents().forEach(styleContents));

  // ---------- per-chapter content hook ----------

  function onContent(contents) {
    const doc = contents.document;
    styleContents(contents);
    doc.addEventListener("click", (e) => onDocClick(e, doc));
    doc.addEventListener("keyup", (e) => {
      if (e.key === "Escape") { closePopover(); hideSelBtn(); }
      else if (e.shiftKey) onSelectionEnd(doc);   // keyboard selection (Shift+arrows)
    });
    doc.addEventListener("mousedown", hideSelBtn);
    // Wait a tick so the selection is final when the mouse/finger is released.
    doc.addEventListener("mouseup", () => setTimeout(() => onSelectionEnd(doc), 0));
    doc.addEventListener("touchend", () => setTimeout(() => onSelectionEnd(doc), 0));
    addSwipe(doc);
    highlightSaved(contents);
  }

  /** Swipe left/right on a touch screen to turn pages. Only a quick, clearly horizontal movement counts,
   *  so tapping a word and long-pressing to select text still work. */
  function addSwipe(doc) {
    let start = null;
    doc.addEventListener("touchstart", (e) => {
      start = e.touches.length === 1
        ? { x: e.touches[0].clientX, y: e.touches[0].clientY, t: Date.now() } : null;
    }, { passive: true });
    // A clearly horizontal drag is ours: stop the browser from treating it as "go back/forward".
    doc.addEventListener("touchmove", (e) => {
      if (!start || e.touches.length !== 1) return;
      const sel = doc.getSelection();
      if (sel && !sel.isCollapsed) return;  // adjusting a text selection
      const dx = e.touches[0].clientX - start.x, dy = e.touches[0].clientY - start.y;
      if (Math.abs(dx) > 10 && Math.abs(dx) > Math.abs(dy) && e.cancelable) e.preventDefault();
    }, { passive: false });
    doc.addEventListener("touchend", (e) => {
      if (!start) return;
      const t = e.changedTouches[0];
      const dx = t.clientX - start.x, dy = t.clientY - start.y, dt = Date.now() - start.t;
      start = null;
      const sel = doc.getSelection();
      if (sel && !sel.isCollapsed) return;  // the user was selecting text
      // A tap (finger barely moved, short touch). Handled here because iPhone Safari sends no click
      // event for a tap on plain text, so the click listener alone would never see it.
      if (Math.abs(dx) < 10 && Math.abs(dy) < 10 && dt < 600) {
        lastTouchTap = Date.now();
        tapAt(doc, t.clientX, t.clientY, doc.elementFromPoint(t.clientX, t.clientY));
        return;
      }
      if (Math.abs(dx) < 50 || Math.abs(dx) < 1.5 * Math.abs(dy) || dt > 800) return;
      closePopover();
      hideSelBtn();
      if (dx < 0) goNext(); else goPrev();
    }, { passive: true });
  }

  // ---------- click → word + sentence ----------

  const WORD_CHAR = /[\p{L}\p{M}\p{N}'’\-­]/u;

  function caretAt(doc, x, y) {
    if (doc.caretPositionFromPoint) {
      const p = doc.caretPositionFromPoint(x, y);
      if (p) return { node: p.offsetNode, offset: p.offset };
    }
    if (doc.caretRangeFromPoint) {
      const r = doc.caretRangeFromPoint(x, y);
      if (r) return { node: r.startContainer, offset: r.startOffset };
    }
    return null;
  }

  /** Expand the caret position to the surrounding word; verify the click actually hit it. */
  function wordAtPoint(doc, x, y) {
    const caret = caretAt(doc, x, y);
    if (!caret || caret.node.nodeType !== Node.TEXT_NODE) return null;
    const text = caret.node.data;
    let start = caret.offset, end = caret.offset;
    while (start > 0 && WORD_CHAR.test(text[start - 1])) start--;
    while (end < text.length && WORD_CHAR.test(text[end])) end++;
    // Trim leading/trailing apostrophes, quotes and hyphens ("'Tis" keeps the inner one).
    while (start < end && /['’\-­]/.test(text[start])) start++;
    while (end > start && /['’\-­]/.test(text[end - 1])) end--;
    if (start >= end) return null;

    const range = doc.createRange();
    range.setStart(caret.node, start);
    range.setEnd(caret.node, end);
    const hit = [...range.getClientRects()].some((r) =>
      x >= r.left - 2 && x <= r.right + 2 && y >= r.top - 2 && y <= r.bottom + 2);
    if (!hit) return null;

    const word = text.slice(start, end).replace(/­/g, "");
    if (!/\p{L}/u.test(word)) return null; // numbers only
    return { word, node: caret.node, offset: start, range };
  }

  const BLOCKS = "p,li,h1,h2,h3,h4,h5,h6,blockquote,td,th,dd,dt,figcaption,pre,div,section,body";
  const ABBREV = /(?:^|\s)(?:Mr|Mrs|Ms|Dr|St|Prof|Mt|Jr|Sr|vs|etc|e\.g|i\.e|z\.\s?B|u\.\s?a|usw|bzw|vgl|ca|Nr|Hr|Fr|Str|S|d\.\s?h)\.\s*$/i;

  /** Return the single sentence in the enclosing block that contains the clicked word. */
  function sentenceAt(hit) {
    const block = hit.node.parentElement.closest(BLOCKS);
    const walker = block.ownerDocument.createTreeWalker(block, NodeFilter.SHOW_TEXT);
    let text = "", pos = -1;
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      if (n === hit.node) pos = text.length + hit.offset;
      text += n.data;
    }
    if (pos < 0) return hit.word;
    // Source line breaks would count as sentence ends; replace 1:1 so offsets stay valid.
    text = text.replace(/\s/g, " ");

    let segments;
    if (window.Intl?.Segmenter) {
      const seg = new Intl.Segmenter(bookLang || undefined, { granularity: "sentence" });
      segments = [...seg.segment(text)].map((s) => ({ start: s.index, text: s.segment }));
    } else {
      segments = [];
      const re = /[^.!?…]+(?:[.!?…]+["'’”»«]*\s*|$)/g;
      for (let m; (m = re.exec(text)) && m[0];) segments.push({ start: m.index, text: m[0] });
    }
    // Re-join segments that were split after a common abbreviation ("Mr. Smith", "z. B.").
    const merged = [];
    for (const s of segments) {
      const last = merged[merged.length - 1];
      if (last && ABBREV.test(last.text)) last.text += s.text;
      else merged.push({ ...s });
    }
    const found = merged.find((s) => pos >= s.start && pos < s.start + s.text.length);
    return (found ? found.text : text).replace(/\s+/g, " ").trim();
  }

  // Time of the last tap handled from touch events (see addSwipe). Some browsers send a click afterwards
  // as well; that click must not look the word up a second time.
  let lastTouchTap = 0;

  function onDocClick(e, doc) {
    if (e.button !== 0) return;
    if (Date.now() - lastTouchTap < 800) return;           // already handled as a tap
    tapAt(doc, e.clientX, e.clientY, e.target);
  }

  /** Look up the word at (x, y) in the book, or close the popup when there is no word. */
  function tapAt(doc, x, y, target) {
    if (target?.closest?.("a[href]")) return;              // let links work
    const sel = doc.getSelection();
    if (sel && !sel.isCollapsed) return;                   // user is selecting text
    const hit = wordAtPoint(doc, x, y);
    if (!hit) { addingTo = null; return closePopover(); }
    const sentence = sentenceAt(hit);
    if (addingTo) return addPart(addingTo, hit.word, sentence);
    const frame = doc.defaultView.frameElement.getBoundingClientRect();
    const wr = hit.range.getBoundingClientRect();
    lookup(hit.word, sentence, {
      left: frame.left + wr.left, right: frame.left + wr.right,
      top: frame.top + wr.top, bottom: frame.top + wr.bottom,
    }, { cfi: cfiOf(doc, hit.range) });
  }

  /** "+ Add word": look the current word up again together with the newly tapped one. */
  function addPart(state, word, sentence) {
    if (sentence !== state.sentence) {
      setStatus("Pick a word from the same sentence.");
      return;
    }
    if (word === state.word || state.parts.includes(word)) return;
    lookup(state.word, state.sentence, state.anchor, { cfi: state.cfi, parts: [...state.parts, word], ask: state.ask });
  }

  /** EPUB position (CFI) of a range in one of the book's iframes, so a saved word can be found again. */
  function cfiOf(doc, range) {
    try {
      return rendition.getContents().find((c) => c.document === doc)?.cfiFromRange(range) || "";
    } catch { return ""; }
  }

  /** Briefly highlight the word we jumped to ("Open in book"). */
  function flashWord(cfi) {
    let range;
    try { range = rendition.getRange(cfi); } catch { return; }
    const win = range?.startContainer?.ownerDocument?.defaultView;
    if (!range || !win?.CSS?.highlights || !win.Highlight) return;
    win.CSS.highlights.set("jump-word", new win.Highlight(range));
    setTimeout(() => win.CSS.highlights.delete("jump-word"), 4000);
  }

  // ---------- selection → Translate button ----------

  function frameRect(doc, r) {
    const f = doc.defaultView.frameElement.getBoundingClientRect();
    return { left: f.left + r.left, right: f.left + r.right, top: f.top + r.top, bottom: f.top + r.bottom };
  }

  let selBtnSuppressedUntil = 0;  // the release after pressing the button must not re-show it

  function onSelectionEnd(doc) {
    if (Date.now() < selBtnSuppressedUntil) return;
    const sel = doc.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return;
    const text = sel.toString().replace(/\s+/g, " ").trim();
    if (!/\p{L}/u.test(text)) return;
    const range = sel.getRangeAt(0).cloneRange();
    const rects = [...range.getClientRects()].filter((r) => r.width > 0);
    if (!rects.length) return;
    const first = frameRect(doc, rects[0]), last = frameRect(doc, rects[rects.length - 1]);
    // Popover anchor: the whole selection's box (so the popover goes below or above it).
    const anchor = { left: first.left, right: last.right, top: first.top, bottom: last.bottom };
    pendingSel = { text, range, anchor, cfi: cfiOf(doc, range) };

    const b = els.selBtn;
    b.hidden = false;
    // Phones: fixed at the bottom (CSS), clear of the system's own copy/paste menu above the selection.
    if (PHONE.matches) { b.style.left = b.style.top = ""; return; }
    b.style.left = `${Math.min(last.right - b.offsetWidth / 2, window.innerWidth - b.offsetWidth - 8)}px`;
    b.style.top = `${Math.min(last.bottom + 6, window.innerHeight - b.offsetHeight - 8)}px`;
  }

  function hideSelBtn() { els.selBtn.hidden = true; }

  // mousedown (not click) so the iframe selection is still there; preventDefault keeps focus off the button.
  els.selBtn.addEventListener("mousedown", (e) => {
    e.preventDefault();
    e.stopPropagation();
    const s = pendingSel;
    hideSelBtn();
    selBtnSuppressedUntil = Date.now() + 600;
    if (s) translateSelection(s);
  });

  /** Short selections (≤ 4 words) are looked up like a word, with their sentence as context;
   *  longer ones are translated as a passage. */
  function translateSelection({ text, range, anchor, cfi }) {
    const phrase = text.replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, "");
    const words = phrase.split(" ").filter(Boolean);
    if (words.length && words.length <= MAX_PHRASE_WORDS && !/[.!?;:]\s/.test(phrase)) {
      const node = range.startContainer;
      const context = node.nodeType === Node.TEXT_NODE
        ? sentenceAt({ node, offset: range.startOffset, word: phrase })
        : phrase;
      lookup(phrase, context.includes(phrase) ? context : phrase, anchor, { cfi });
    } else {
      translatePassage(text, anchor);
    }
  }

  async function translatePassage(text, anchor, refresh = false, ask = false) {
    if (inflight) inflight.abort();
    const ctrl = (inflight = new AbortController());
    const seq = ++lookupSeq;
    const state = { text, anchor, lang: ["en", "de"].includes(bookLang) ? bookLang : null, data: null };

    if (text.length > MAX_PASSAGE_CHARS) {
      state.error = `The selection is too long (${text.length} characters). ` +
        `Select at most about ${MAX_PASSAGE_CHARS} characters (3–4 sentences).`;
      renderPassage(state, "error");
      return positionPopover(anchor);
    }
    renderPassage(state, "loading");
    positionPopover(anchor);
    try {
      const r = await fetch("/translate", {
        method: "POST",
        headers: { "Content-Type": "application/json; charset=utf-8" },
        body: JSON.stringify({ text, lang_hint: bookLang || null, refresh, target: target() }),
        signal: ctrl.signal,
      });
      const data = await r.json().catch(() => ({}));
      if (seq !== lookupSeq) return;
      if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Server error (${r.status})`);
      state.data = data;
      state.lang = data.lang;
      renderPassage(state, "done");
    } catch (e) {
      if (e.name === "AbortError" || seq !== lookupSeq) return;
      state.error = e instanceof TypeError ? "Could not reach the ReaderHelper server." : e.message;
      renderPassage(state, "error");
    } finally {
      if (inflight === ctrl) inflight = null;
    }
    positionPopover(anchor);
  }

  function renderPassage(state, phase) {
    const d = state.data;
    const L = LABELS[d?.target_lang] || LABELS[target()];
    let html = `<div class="head">
      <span class="word">${esc(L.passage)}</span>
      ${state.lang ? `<span class="lang">${esc(state.lang)}</span>` : ""}
      <button class="icon" data-act="speak" title="Read the original aloud">🔊</button>
      <button class="close" data-act="close" title="Close (Esc)">×</button>
    </div>
    <div class="section sentence original">${esc(state.text)}</div>`;
    if (phase === "loading") {
      html += `<div class="section loading"><span class="spinner"></span>Translating… this can take 10–20 seconds.</div>`;
    } else if (phase === "error") {
      html += `<div class="section error">${esc(state.error)}</div>`;
    } else {
      if (d.translation_vi) {
        html += `<div class="section"><div class="label">${esc(L.tr)}</div><div class="passage-vi">${esc(d.translation_vi)}</div></div>`;
      }
      if (d.notes?.length) {
        html += `<div class="section"><div class="label">${esc(L.notes)}</div><ul class="notes">` +
          d.notes.map((n) => `<li><b>${esc(n.term)}</b>: ${esc(n.explanation_vi)}</li>`).join("") + `</ul></div>`;
      }
      if (d.warning) html += `<div class="section warning">${esc(d.warning)}</div>`;
      html += `<div class="actions">
        <button data-act="retry" title="Ignore the cached answer and ask the model again">↻ Ask again</button>
        ${d.cached ? '<span class="sense">cached</span>' : ""}</div>`;
    }
    els.popover.classList.add("wide");
    els.popover.innerHTML = html;
    els.popover.hidden = false;
    els.popover.onclick = (ev) => {
      const act = ev.target.closest("[data-act]")?.dataset.act;
      if (act === "close") closePopover();
      if (act === "speak") speak(state.text, state.lang || "en");
      if (act === "retry") translatePassage(state.text, state.anchor, true, true);
      if (act === "ask") translatePassage(state.text, state.anchor, false, true);
    };
  }

  // ---------- lookup + popover ----------

  /** Look up `word` in `sentence`. Options: refresh (skip the cache), cfi (position, for saving),
   *  ask (use the AI even if it is switched off), parts (more words of an expression, "+ Add word"),
   *  single (only this word, no expression around it). */
  async function lookup(word, sentence, anchor, opts = {}) {
    const { refresh = false, cfi = "", ask = false, parts = [], single = false } = opts;
    if (inflight) inflight.abort();
    const ctrl = (inflight = new AbortController());
    const seq = ++lookupSeq;
    addingTo = null;
    const hintLang = ["en", "de"].includes(bookLang) ? bookLang : null;
    const state = { word, sentence, anchor, cfi, parts, single, ask, lang: hintLang, data: null };

    renderPopover(state, "loading");
    positionPopover(anchor);
    if (autoplay && hintLang) speak(word, hintLang);

    const post = async (quick) => {
      const r = await fetch("/lookup", {
        method: "POST",
        headers: { "Content-Type": "application/json; charset=utf-8" },
        body: JSON.stringify({ word, sentence, lang_hint: bookLang || null, refresh, target: target(), quick,
                               parts, single }),
        signal: ctrl.signal,
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Server error (${r.status})`);
      return data;
    };

    try {
      // Step 1 (fast): base form, dictionary, audio. A cached answer comes back complete right away.
      let data = await post(true);
      if (seq !== lookupSeq) return;
      state.data = data;
      state.lang = data.lang;
      if (autoplay && !hintLang) speak(word, data.lang);
      if (data.pending && !ask) {
        // Words: the dictionary result is the answer; "Ask AI" gets the meaning in this sentence.
        // (An AI answer from earlier comes back complete from the cache.)
        renderPopover(state, "dict");
        positionPopover(anchor);
        return;
      }
      if (data.pending) {
        renderPopover(state, "partial");
        positionPopover(anchor);
        // Step 2 (slow): the model's meaning for this sentence.
        data = await post(false);
        if (seq !== lookupSeq) return;
        state.data = data;
      }
      renderPopover(state, "done");
    } catch (e) {
      if (e.name === "AbortError" || seq !== lookupSeq) return;
      state.error = e instanceof TypeError ? "Could not reach the ReaderHelper server." : e.message;
      renderPopover(state, "error");
    } finally {
      if (inflight === ctrl) inflight = null;
    }
    positionPopover(anchor);
  }

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function renderPopover(state, phase) {
    const d = state.data;
    const L = LABELS[d?.target_lang] || LABELS[target()];
    const lemma = d?.lemma && d.lemma.toLowerCase() !== state.word.toLowerCase()
      ? `<span class="lemma">← ${esc(d.lemma)}</span>` : "";
    const ex = d?.expression;
    let html = `<div class="head">
      <span class="word">${esc(ex ? ex.display : state.word)}</span>${ex ? "" : lemma}
      ${state.lang ? `<span class="lang">${esc(state.lang)}</span>` : ""}
      <button class="icon" data-act="speak" title="Pronounce">🔊</button>
      <button class="close" data-act="close" title="Close (Esc)">×</button>
    </div>`;
    const g = d?.grammar;
    if (g) {
      html += `<div class="grammar"${g.guess ? ' title="Gender read from the sentence; no dictionary table for this word"' : ""}>
        ${grammarHtml(g.text, g.gender)}${g.irregular ? ' <span class="tag">unregelmäßig</span>' : ""}</div>`;
    }
    if (ex) {
      const how = { list: "Fixed expression", dictionary: "Expression (from the dictionary)",
                    manual: "Your own combination" }[ex.source] || "Expression";
      html += `<div class="expr-note">${esc(how)} · in this sentence: <b>${esc(ex.words)}</b>
        <button class="link-btn" data-act="single" title="Look up only the word you tapped">Just “${esc(state.word)}”</button></div>`;
    }
    if (addingTo === state) {
      html += `<div class="section adding">Tap another word of this sentence to add it… <button class="link-btn" data-act="cancel-add">Cancel</button></div>`;
    }

    if (phase === "loading") {
      html += `<div class="section loading"><span class="spinner"></span>Looking up…</div>`;
    } else if (phase === "error") {
      html += `<div class="section error">${esc(state.error)}</div>`;
    } else {
      // "partial": dictionary is here, the model's answer is still coming.
      if (phase === "partial") {
        html += `<div class="section loading"><span class="spinner"></span>Finding the meaning in this sentence…</div>`;
      }
      if (d.translation_vi) {
        html += `<div class="section"><div class="label">${esc(L.tr)}</div>
          <div class="vi">${esc(d.translation_vi)}</div>
          ${d.sense_vi ? `<div class="sense">${esc(d.sense_vi)}</div>` : ""}</div>`;
      }
      if (d.gloss_vi) {
        html += `<div class="section"><div class="label">${esc(L.gloss)}</div><div>${esc(d.gloss_vi)}</div></div>`;
      }
      if (d.warning) html += `<div class="section warning">${esc(d.warning)}</div>`;
      const senses = d.senses || [];
      const label = d.definition_of ? `Definition of “${d.definition_of}” (no dictionary entry for the expression)`
        : d.sense_no ? `Definition · sense ${d.sense_no} of ${senses.length} (chosen for this sentence)` : "Definition";
      html += `<div class="section"><div class="label">${esc(label)}</div>` +
        (d.definition ? `<div class="def">${esc(d.definition)}</div>`
                      : `<div class="note">${esc(d.definition_note || "No definition found.")}</div>`);
      if (senses.length > 1) {
        html += `<details class="senses"><summary>All ${senses.length} senses</summary><ol>` +
          senses.map((s, i) => `<li${i + 1 === d.sense_no ? ' class="chosen"' : ""}>${esc(s)}</li>`).join("") +
          `</ol></details>`;
      }
      html += `</div>`;
      html += `<div class="section sentence">${esc(d.sentence || state.sentence)}` +
        (d.sentence_tr ? `<div class="sentence-tr">${esc(d.sentence_tr)}</div>` : "") + `</div>`;
      if (phase === "dict") {
        html += `<div class="actions"><button class="save" data-act="save">Save ${ex ? "expression" : "word"}</button>
          <button data-act="ask" title="Get the meaning in this sentence from the AI (takes a while)">✨ Ask AI</button>
          <button data-act="add" title="Add another word of this sentence (for expressions like “in Frage stellen”)">+ Add word</button></div>`;
      }
      if (phase === "done") {
        html += `<div class="actions"><button class="save" data-act="save">Save ${ex ? "expression" : "word"}</button>
          <button data-act="retry" title="Ignore the cached answer and ask the model again">↻ Ask again</button>
          <button data-act="add" title="Add another word of this sentence (for expressions like “in Frage stellen”)">+ Add word</button>
          ${d.cached ? '<span class="sense">cached</span>' : ""}</div>`;
      }
    }
    els.popover.classList.remove("wide");
    els.popover.innerHTML = html;
    els.popover.hidden = false;

    els.popover.onclick = async (ev) => {
      const act = ev.target.closest("[data-act]")?.dataset.act;
      if (act === "close") closePopover();
      if (act === "speak") speak(state.word, state.lang || "en");
      if (act === "save") saveWord(state, ev.target);
      const again = { cfi: state.cfi, parts: state.parts, single: state.single, ask: state.ask };
      if (act === "retry") lookup(state.word, state.sentence, state.anchor, { ...again, refresh: true, ask: true });
      if (act === "ask") lookup(state.word, state.sentence, state.anchor, { ...again, ask: true });
      if (act === "single") lookup(state.word, state.sentence, state.anchor, { cfi: state.cfi, single: true, ask: state.ask });
      if (act === "add") { addingTo = state; renderPopover(state, phase); }
      if (act === "cancel-add") { addingTo = null; renderPopover(state, phase); }
    };
  }

  // On phones the popover is a bottom sheet and the Translate button sits at the bottom (CSS).
  const PHONE = window.matchMedia("(max-width: 600px)");
  PHONE.addEventListener("change", () => setBars(true));

  function positionPopover(a) {
    const p = els.popover, m = 8;
    if (PHONE.matches) { p.style.left = p.style.top = ""; return; }
    const w = p.offsetWidth, h = p.offsetHeight;
    let left = Math.min(Math.max(m, a.left), window.innerWidth - w - m);
    let top = a.bottom + m;
    if (top + h > window.innerHeight - m) top = Math.max(m, a.top - h - m);
    p.style.left = `${left}px`;
    p.style.top = `${top}px`;
  }

  function closePopover() {
    addingTo = null;
    if (inflight) { inflight.abort(); inflight = null; }
    lookupSeq++;
    els.popover.hidden = true;
  }

  let closedPopoverAt = 0;  // a tap that only closes the popup shouldn't also toggle the bars (focus mode)
  document.addEventListener("pointerdown", (e) => {  // pointerdown: see the chapter menu above
    if (!els.popover.hidden && !els.popover.contains(e.target)) { closePopover(); closedPopoverAt = Date.now(); }
  });

  function speak(text, lang) {
    if (!("speechSynthesis" in window)) return;
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = lang === "de" ? "de-DE" : "en-US";
    u.rate = 0.9;
    const voice = speechSynthesis.getVoices().find((v) => v.lang.replace("_", "-") === u.lang);
    if (voice) u.voice = voice;
    speechSynthesis.speak(u);
  }
  window.speechSynthesis?.getVoices(); // warm up voice list (async in Chrome)

  // ---------- vocab ----------

  /** What to store as the word's meaning. Without an AI answer (AI off, or it failed) the chosen
   *  English dictionary meaning becomes the note; a Vietnamese meaning can be added later with Edit. */
  function meaningToSave(d) {
    if (d.translation_vi || d.gloss_vi) {
      return { translation_vi: d.translation_vi, gloss_vi: d.gloss_vi, target_lang: d.target_lang || "vi" };
    }
    return { translation_vi: "", gloss_vi: d.definition || "", target_lang: "en" };
  }

  async function saveWord(state, btn) {
    const d = state.data;
    btn.disabled = true;
    try {
      const r = await fetch("/api/vocab", {
        method: "POST",
        headers: { "Content-Type": "application/json; charset=utf-8" },
        body: JSON.stringify({
          lang: d.lang, word: d.expression ? d.expression.words : d.word, lemma: d.lemma, sentence: d.sentence,
          ...meaningToSave(d),
          sentence_tr: d.sentence_tr || "", book_id: currentBookId, cfi: state.cfi || "",
          grammar: d.grammar?.text || "",
        }),
      });
      if (!r.ok) throw new Error();
      btn.textContent = "Saved ✓";
      await loadSavedWords();  // refresh the saved list (for highlighting) and the vocabulary count
      rendition?.getContents().forEach(highlightSaved);
    } catch {
      btn.disabled = false;
      btn.textContent = "Save failed – retry";
    }
  }

  async function loadSavedWords() {
    try {
      const items = await (await fetch("/api/vocab")).json();
      savedWords = new Set(items.map((v) => v.word.toLowerCase()));
      // Phrases are matched by spelling; expressions with gaps ("stellte … in Frage") by the server.
      savedPhrases = new Set(items.filter((v) => v.word.trim().includes(" ") && !v.word.includes("…"))
        .map((v) => v.word.toLowerCase()));
      els.vocabCount.textContent = items.length;
      els.vocabCount.hidden = items.length === 0;
    } catch { /* highlighting and the count are optional */ }
  }

  /** Highlight saved words with the CSS Custom Highlight API (no DOM changes, so CFIs stay valid).
   *  Single words are matched by their base form on the server, with the same analysis as a click: a
   *  saved "fangen" marks "fing"/"fängt" but not "fing … an" (that is anfangen). Phrases (several words),
   *  books in other languages, or a failed request fall back to matching the spelling. */
  async function highlightSaved(contents) {
    const win = contents.window, doc = contents.document;
    if (!win?.CSS?.highlights || !win.Highlight) return;
    if (!markSaved || savedWords.size === 0) { win.CSS.highlights.delete("saved-word"); return; }
    let ranges = spellingMatches(doc, savedPhrases);
    if (["en", "de"].includes(bookLang)) {
      try {
        ranges = ranges.concat(await baseFormMatches(doc));
      } catch {
        ranges = spellingMatches(doc, savedWords);  // server unreachable: spelling only
      }
    } else {
      ranges = spellingMatches(doc, savedWords);
    }
    if (!doc.defaultView) return;  // the chapter was replaced meanwhile
    win.CSS.highlights.set("saved-word", new win.Highlight(...ranges));
  }

  /** Text of the book page, grouped by paragraph (block element), with where each text node starts. */
  function paragraphsOf(doc) {
    const byBlock = new Map();
    const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const el = n.parentElement?.closest(BLOCKS) || doc.body;
      let b = byBlock.get(el);
      if (!b) { b = { text: "", nodes: [] }; byBlock.set(el, b); }
      b.nodes.push({ node: n, start: b.text.length });
      b.text += n.data;
    }
    return [...byBlock.values()].filter((b) => /\p{L}/u.test(b.text));
  }

  /** Text node + offset for a character position inside a paragraph. */
  function pointIn(b, offset) {
    let i = b.nodes.length - 1;
    while (i > 0 && b.nodes[i].start > offset) i--;
    return [b.nodes[i].node, offset - b.nodes[i].start];
  }

  async function baseFormMatches(doc) {
    const paras = paragraphsOf(doc);
    if (!paras.length) return [];
    const r = await fetch("/api/highlights", {
      method: "POST",
      headers: { "Content-Type": "application/json; charset=utf-8" },
      body: JSON.stringify({ lang: bookLang, paragraphs: paras.map((b) => b.text) }),
    });
    if (!r.ok) throw new Error(`highlights ${r.status}`);
    const { spans } = await r.json();
    const ranges = [];
    spans.forEach((list, i) => {
      for (const [s, e] of list) {
        const range = doc.createRange();
        range.setStart(...pointIn(paras[i], s));
        range.setEnd(...pointIn(paras[i], e));
        ranges.push(range);
      }
    });
    return ranges;
  }

  /** Whole-word matches of the given spellings (used for phrases and as a fallback). */
  function spellingMatches(doc, words) {
    if (!words.size) return [];
    const escRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    // Group 1 is the character before the word (or nothing at the start); written without a
    // lookbehind "(?<!…)", which iPhones before iOS 16.4 don't support.
    const re = new RegExp(`(^|[^\\p{L}\\p{M}])(${[...words].map(escRe).join("|")})(?![\\p{L}\\p{M}])`, "giu");
    const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
    const ranges = [];
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      re.lastIndex = 0;
      for (let m; (m = re.exec(n.data));) {
        const start = m.index + m[1].length;
        const r = doc.createRange();
        r.setStart(n, start);
        r.setEnd(n, start + m[2].length);
        ranges.push(r);
        if (m[2].length === 0) re.lastIndex++;  // safety: never loop on an empty match
      }
    }
    return ranges;
  }

  // ---------- init ----------
  renderTarget();
  renderSpread();
  renderMark();
  renderAutoplay();
  setFont(fontPct);
  loadSavedWords().then(loadBookFromUrl);
})();
