/* ReaderHelper – EPUB reader with click-to-lookup. Vanilla JS, no build step. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const els = {
    bookSelect: $("bookSelect"), upload: $("upload"), toc: $("tocSelect"),
    viewer: $("viewer"), prev: $("prev"), next: $("next"),
    status: $("status"), popover: $("popover"), autoplay: $("autoplay"), selBtn: $("selBtn"),
  };
  const MAX_PASSAGE_CHARS = 600;  // keep in sync with backend/main.py
  const MAX_PHRASE_WORDS = 4;     // longer selections are translated as a passage
  let pendingSel = null;          // selection waiting for the Translate button

  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* ignore */ } },
  };

  let book = null, rendition = null, bookName = "", bookLang = "";
  let inflight = null;          // AbortController of the current /lookup
  let lookupSeq = 0;            // guards against late responses
  let savedWords = new Set();   // lowercase forms for highlighting

  // ---------- books ----------

  async function loadBookList(selectName) {
    try {
      const names = await (await fetch("/api/books")).json();
      els.bookSelect.length = 1;
      for (const n of names) els.bookSelect.add(new Option(n, n));
      const want = selectName || store.get("lastBook");
      if (want && names.includes(want)) {
        els.bookSelect.value = want;
        openBook(want);
      }
    } catch {
      setStatus("Could not load the book list from the server.");
    }
  }

  els.bookSelect.addEventListener("change", () => els.bookSelect.value && openBook(els.bookSelect.value));

  els.upload.addEventListener("change", async () => {
    const file = els.upload.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    setStatus("Uploading…");
    try {
      const r = await fetch("/api/books", { method: "POST", body: fd });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || "Upload failed");
      await loadBookList(data.name);
    } catch (e) {
      setStatus(`Upload failed: ${e.message}`);
    }
    els.upload.value = "";
  });

  async function openBook(name) {
    closePopover();
    if (rendition) rendition.destroy();
    if (book) book.destroy();
    els.viewer.innerHTML = "";
    bookName = name;
    store.set("lastBook", name);
    setStatus("Opening book…");

    book = ePub(`/books/${encodeURIComponent(name)}`, { openAs: "epub" });
    rendition = book.renderTo(els.viewer, { width: "100%", height: "100%", flow: "paginated", spread: "none" });
    rendition.themes.default({
      body: { "line-height": "1.6 !important", cursor: "default" },
      "::highlight(saved-word)": { "background-color": "#fde68a" },
    });
    rendition.hooks.content.register(onContent);
    rendition.on("relocated", onRelocated);
    rendition.on("keyup", onKey);

    try {
      await book.ready;
      bookLang = (book.packaging.metadata.language || "").slice(0, 2).toLowerCase();
      await rendition.display(store.get(`pos:${name}`) || undefined);
      els.prev.disabled = els.next.disabled = false;
      buildToc();
    } catch (e) {
      console.error(e);
      setStatus(`Could not open this EPUB: ${e.message || e}`);
    }
  }

  async function buildToc() {
    const nav = await book.loaded.navigation;
    els.toc.length = 1;
    const add = (items, depth) => items.forEach((it) => {
      els.toc.add(new Option(`${"  ".repeat(depth)}${it.label.trim()}`, it.href));
      if (it.subitems?.length) add(it.subitems, depth + 1);
    });
    add(nav.toc, 0);
    els.toc.disabled = nav.toc.length === 0;
  }
  els.toc.addEventListener("change", () => { if (els.toc.value) rendition.display(els.toc.value); });

  function onRelocated(loc) {
    hideSelBtn();
    store.set(`pos:${bookName}`, loc.start.cfi);
    const total = book.spine.length;
    setStatus(`${bookName} · section ${loc.start.index + 1} / ${total}` +
      (loc.atEnd ? " · end of book" : ""));
  }

  els.prev.addEventListener("click", () => rendition?.prev());
  els.next.addEventListener("click", () => rendition?.next());

  function onKey(e) {
    if (e.key === "Escape") return closePopover();
    if (!rendition || e.target?.tagName === "SELECT") return;
    if (e.key === "ArrowLeft") rendition.prev();
    if (e.key === "ArrowRight") rendition.next();
  }
  document.addEventListener("keyup", onKey);

  function setStatus(msg) { els.status.textContent = msg; }

  // ---------- per-chapter content hook ----------

  function onContent(contents) {
    const doc = contents.document;
    doc.addEventListener("click", (e) => onDocClick(e, doc));
    doc.addEventListener("keyup", (e) => {
      if (e.key === "Escape") { closePopover(); hideSelBtn(); }
      else if (e.shiftKey) onSelectionEnd(doc);   // keyboard selection (Shift+arrows)
    });
    doc.addEventListener("mousedown", hideSelBtn);
    // Wait a tick so the selection is final when the mouse/finger is released.
    doc.addEventListener("mouseup", () => setTimeout(() => onSelectionEnd(doc), 0));
    doc.addEventListener("touchend", () => setTimeout(() => onSelectionEnd(doc), 0));
    highlightSaved(contents);
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

  function onDocClick(e, doc) {
    if (e.button !== 0) return;
    if (e.target.closest?.("a[href]")) return;             // let links work
    const sel = doc.getSelection();
    if (sel && !sel.isCollapsed) return;                   // user is selecting text
    const hit = wordAtPoint(doc, e.clientX, e.clientY);
    if (!hit) return closePopover();
    const sentence = sentenceAt(hit);
    const frame = doc.defaultView.frameElement.getBoundingClientRect();
    const wr = hit.range.getBoundingClientRect();
    lookup(hit.word, sentence, {
      left: frame.left + wr.left, right: frame.left + wr.right,
      top: frame.top + wr.top, bottom: frame.top + wr.bottom,
    });
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
    pendingSel = { text, range, anchor };

    const b = els.selBtn;
    b.hidden = false;
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
  function translateSelection({ text, range, anchor }) {
    const phrase = text.replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, "");
    const words = phrase.split(" ").filter(Boolean);
    if (words.length && words.length <= MAX_PHRASE_WORDS && !/[.!?;:]\s/.test(phrase)) {
      const node = range.startContainer;
      const context = node.nodeType === Node.TEXT_NODE
        ? sentenceAt({ node, offset: range.startOffset, word: phrase })
        : phrase;
      lookup(phrase, context.includes(phrase) ? context : phrase, anchor);
    } else {
      translatePassage(text, anchor);
    }
  }

  async function translatePassage(text, anchor, refresh = false) {
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
        body: JSON.stringify({ text, lang_hint: bookLang || null, refresh }),
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
    let html = `<div class="head">
      <span class="word">Bản dịch</span>
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
        html += `<div class="section"><div class="label">Tiếng Việt</div><div class="passage-vi">${esc(d.translation_vi)}</div></div>`;
      }
      if (d.notes?.length) {
        html += `<div class="section"><div class="label">Ghi chú</div><ul class="notes">` +
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
      if (act === "retry") translatePassage(state.text, state.anchor, true);
    };
  }

  // ---------- lookup + popover ----------

  async function lookup(word, sentence, anchor, refresh = false) {
    if (inflight) inflight.abort();
    const ctrl = (inflight = new AbortController());
    const seq = ++lookupSeq;
    const hintLang = ["en", "de"].includes(bookLang) ? bookLang : null;
    const state = { word, sentence, anchor, lang: hintLang, data: null };

    renderPopover(state, "loading");
    positionPopover(anchor);
    if (els.autoplay.checked && hintLang) speak(word, hintLang);

    try {
      const r = await fetch("/lookup", {
        method: "POST",
        headers: { "Content-Type": "application/json; charset=utf-8" },
        body: JSON.stringify({ word, sentence, lang_hint: bookLang || null, refresh }),
        signal: ctrl.signal,
      });
      const data = await r.json().catch(() => ({}));
      if (seq !== lookupSeq) return;
      if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Server error (${r.status})`);
      state.data = data;
      state.lang = data.lang;
      if (els.autoplay.checked && !hintLang) speak(word, data.lang);
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
    const lemma = d?.lemma && d.lemma.toLowerCase() !== state.word.toLowerCase()
      ? `<span class="lemma">← ${esc(d.lemma)}</span>` : "";
    let html = `<div class="head">
      <span class="word">${esc(state.word)}</span>${lemma}
      ${state.lang ? `<span class="lang">${esc(state.lang)}</span>` : ""}
      <button class="icon" data-act="speak" title="Pronounce">🔊</button>
      <button class="close" data-act="close" title="Close (Esc)">×</button>
    </div>`;

    if (phase === "loading") {
      html += `<div class="section loading"><span class="spinner"></span>Looking up… the first lookup can take a while while the model loads.</div>`;
    } else if (phase === "error") {
      html += `<div class="section error">${esc(state.error)}</div>`;
    } else {
      if (d.translation_vi) {
        html += `<div class="section"><div class="label">Tiếng Việt</div>
          <div class="vi">${esc(d.translation_vi)}</div>
          ${d.sense_vi ? `<div class="sense">${esc(d.sense_vi)}</div>` : ""}</div>`;
      }
      if (d.gloss_vi) {
        html += `<div class="section"><div class="label">Nghĩa trong câu này</div><div>${esc(d.gloss_vi)}</div></div>`;
      }
      if (d.warning) html += `<div class="section warning">${esc(d.warning)}</div>`;
      const senses = d.senses || [];
      const label = d.sense_no ? `Definition · sense ${d.sense_no} of ${senses.length} (chosen for this sentence)` : "Definition";
      html += `<div class="section"><div class="label">${esc(label)}</div>` +
        (d.definition ? `<div class="def">${esc(d.definition)}</div>`
                      : `<div class="note">${esc(d.definition_note || "No definition found.")}</div>`);
      if (senses.length > 1) {
        html += `<details class="senses"><summary>All ${senses.length} senses</summary><ol>` +
          senses.map((s, i) => `<li${i + 1 === d.sense_no ? ' class="chosen"' : ""}>${esc(s)}</li>`).join("") +
          `</ol></details>`;
      }
      html += `</div>`;
      html += `<div class="section sentence">${esc(d.sentence || state.sentence)}</div>`;
      html += `<div class="actions"><button class="save" data-act="save">Save word</button>
        <button data-act="retry" title="Ignore the cached answer and ask the model again">↻ Ask again</button>
        ${d.cached ? '<span class="sense">cached</span>' : ""}</div>`;
    }
    els.popover.classList.remove("wide");
    els.popover.innerHTML = html;
    els.popover.hidden = false;

    els.popover.onclick = async (ev) => {
      const act = ev.target.closest("[data-act]")?.dataset.act;
      if (act === "close") closePopover();
      if (act === "speak") speak(state.word, state.lang || "en");
      if (act === "save") saveWord(state, ev.target);
      if (act === "retry") lookup(state.word, state.sentence, state.anchor, true);
    };
  }

  function positionPopover(a) {
    const p = els.popover, m = 8;
    const w = p.offsetWidth, h = p.offsetHeight;
    let left = Math.min(Math.max(m, a.left), window.innerWidth - w - m);
    let top = a.bottom + m;
    if (top + h > window.innerHeight - m) top = Math.max(m, a.top - h - m);
    p.style.left = `${left}px`;
    p.style.top = `${top}px`;
  }

  function closePopover() {
    if (inflight) { inflight.abort(); inflight = null; }
    lookupSeq++;
    els.popover.hidden = true;
  }

  document.addEventListener("mousedown", (e) => {
    if (!els.popover.hidden && !els.popover.contains(e.target)) closePopover();
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

  async function saveWord(state, btn) {
    const d = state.data;
    btn.disabled = true;
    try {
      const r = await fetch("/api/vocab", {
        method: "POST",
        headers: { "Content-Type": "application/json; charset=utf-8" },
        body: JSON.stringify({
          lang: d.lang, word: d.word, lemma: d.lemma, sentence: d.sentence,
          translation_vi: d.translation_vi, gloss_vi: d.gloss_vi,
        }),
      });
      if (!r.ok) throw new Error();
      btn.textContent = "Saved ✓";
      savedWords.add(d.word.toLowerCase());
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
    } catch { /* highlighting is optional */ }
  }

  /** Highlight saved words with the CSS Custom Highlight API (no DOM changes, so CFIs stay valid). */
  function highlightSaved(contents) {
    const win = contents.window;
    if (!win.CSS?.highlights || !win.Highlight || savedWords.size === 0) return;
    const escRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const re = new RegExp(`(?<![\\p{L}\\p{M}])(?:${[...savedWords].map(escRe).join("|")})(?![\\p{L}\\p{M}])`, "giu");
    const doc = contents.document;
    const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
    const ranges = [];
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      re.lastIndex = 0;
      for (let m; (m = re.exec(n.data));) {
        const r = doc.createRange();
        r.setStart(n, m.index);
        r.setEnd(n, m.index + m[0].length);
        ranges.push(r);
      }
    }
    win.CSS.highlights.set("saved-word", new win.Highlight(...ranges));
  }

  // ---------- init ----------
  els.autoplay.checked = store.get("autoplay") === "1";
  els.autoplay.addEventListener("change", () => store.set("autoplay", els.autoplay.checked ? "1" : "0"));
  loadSavedWords().then(() => loadBookList());
})();
