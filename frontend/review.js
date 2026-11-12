/* ReaderHelper – review saved words as flashcards (spaced repetition, see backend/srs.py). */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  let queue = [];        // cards still to show in this session
  let total = 0;         // cards in this session (for the progress bar)
  let doneCount = 0;
  let current = null;
  let revealed = false;
  let counts = null;
  const bookFilter = $("bookFilter");

  // ---------- loading ----------

  async function loadBooks() {
    try {
      const books = (await (await fetch("/api/books")).json()).filter((b) => b.word_count > 0);
      for (const b of books) bookFilter.add(new Option(`${b.title} (${b.word_count})`, b.id));
      const want = new URLSearchParams(location.search).get("book");
      if (want && books.some((b) => String(b.id) === want)) bookFilter.value = want;
    } catch { /* filter is optional */ }
  }

  async function loadQueue() {
    const q = bookFilter.value ? `?book_id=${encodeURIComponent(bookFilter.value)}` : "";
    try {
      const data = await (await fetch(`/api/review${q}`)).json();
      queue = data.cards;
      counts = data.counts;
      total = queue.length;
      doneCount = 0;
      next();
    } catch {
      showError("Could not load your words from the server.");
    }
  }

  bookFilter.addEventListener("change", () => {
    const url = new URL(location.href);
    if (bookFilter.value) url.searchParams.set("book", bookFilter.value); else url.searchParams.delete("book");
    history.replaceState(null, "", url);
    loadQueue();
  });

  // ---------- one card ----------

  /** The sentence with the word marked, so it is learned in its context. */
  function markedSentence(sentence, word) {
    const i = sentence.toLowerCase().indexOf(word.toLowerCase());
    if (i < 0) return esc(sentence);
    return esc(sentence.slice(0, i)) + `<mark>${esc(sentence.slice(i, i + word.length))}</mark>` +
      esc(sentence.slice(i + word.length));
  }

  function renderCounts() {
    const left = queue.length + (current ? 1 : 0);
    $("counts").textContent = total ? `${Math.min(doneCount + 1, total)} of ${total}` +
      (counts ? ` · ${counts.due} due · ${counts.new} new` : "") : "";
    $("progressFill").style.width = total ? `${Math.round((doneCount / total) * 100)}%` : "0";
    return left;
  }

  function next() {
    current = queue.shift() || null;
    revealed = false;
    renderCounts();
    if (!current) return showDone();
    const c = current;
    $("done").hidden = true;
    $("card").hidden = false;
    $("cLang").textContent = c.lang;
    $("cBook").textContent = c.book_title ? `from ${c.book_title}` : "";
    $("cWord").textContent = c.word;
    $("cLemma").textContent = c.lemma && c.lemma.toLowerCase() !== c.word.toLowerCase() ? `base form: ${c.lemma}` : "";
    $("cSentence").innerHTML = markedSentence(c.sentence, c.word);
    $("cTranslation").textContent = c.translation_vi || "—";
    $("cSense").textContent = "";
    $("cGloss").textContent = c.gloss_vi || "";
    $("cSentenceTr").textContent = c.sentence_tr || "";
    const open = $("cOpen");
    open.hidden = !c.book_id;
    if (c.book_id) {
      open.href = `/read?book=${encodeURIComponent(c.book_id)}` + (c.cfi ? `&cfi=${encodeURIComponent(c.cfi)}` : "");
      open.textContent = c.cfi ? "Open in book →" : "Open the book →";
    }
    for (const b of $("grades").querySelectorAll("[data-grade]")) {
      b.querySelector("small").textContent = c.preview?.[b.dataset.grade] || "";
    }
    $("answer").hidden = true;
    $("grades").hidden = true;
    $("showBtn").hidden = false;
    $("fcHint").hidden = false;
    $("showBtn").focus();
  }

  function reveal() {
    if (!current || revealed) return;
    revealed = true;
    $("answer").hidden = false;
    $("grades").hidden = false;
    $("showBtn").hidden = true;
    $("fcHint").hidden = true;
    $("grades").querySelector(".g-good").focus();
  }

  async function grade(g) {
    if (!current || !revealed) return;
    const card = current;
    $("grades").querySelectorAll("button").forEach((b) => { b.disabled = true; });
    try {
      const r = await fetch(`/api/vocab/${card.id}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json; charset=utf-8" },
        body: JSON.stringify({ grade: g }),
      });
      if (!r.ok) throw new Error();
      const updated = await r.json();
      // Forgotten words come back at the end of this session (they are due again in 10 minutes).
      if (g === "again") { queue.push({ ...card, ...updated }); total += 1; }
      if (counts) {
        if (card.due_at == null) counts.new = Math.max(0, counts.new - 1);
        else counts.due = Math.max(0, counts.due - 1);
      }
      doneCount += 1;
      next();
    } catch {
      showError("Could not save the answer. Is the server running?");
    } finally {
      $("grades").querySelectorAll("button").forEach((b) => { b.disabled = false; });
    }
  }

  // ---------- done / errors ----------

  function untilText(iso) {
    const mins = Math.round((new Date(iso) - Date.now()) / 60000);
    if (mins < 60) return `in ${Math.max(1, mins)} min`;
    if (mins < 60 * 24) return `in ${Math.round(mins / 60)} h`;
    const days = Math.round(mins / 1440);
    return days === 1 ? "tomorrow" : `in ${days} days`;
  }

  async function showDone() {
    $("card").hidden = true;
    $("done").hidden = false;
    $("progressFill").style.width = total ? "100%" : "0";
    const q = bookFilter.value ? `?book_id=${encodeURIComponent(bookFilter.value)}` : "";
    let c = counts;
    try { c = await (await fetch(`/api/review/counts${q}`)).json(); } catch { /* use what we have */ }
    $("counts").textContent = c ? `${c.total} saved words` : "";
    if (!c || c.total === 0) {
      $("doneTitle").textContent = "No saved words yet";
      $("doneText").textContent = "Click a word while reading and press “Save word”. It will show up here for review.";
    } else {
      $("doneTitle").textContent = doneCount ? `Done – ${doneCount} reviewed` : "Nothing to review right now";
      $("doneText").textContent = c.next_due
        ? `The next word is due ${untilText(c.next_due)}. Words you know well come back less often.`
        : "All your words are scheduled.";
    }
  }

  function showError(msg) {
    $("error").textContent = msg;
    $("error").hidden = false;
  }

  // ---------- input ----------

  $("showBtn").addEventListener("click", reveal);
  $("grades").addEventListener("click", (e) => {
    const b = e.target.closest("[data-grade]");
    if (b) grade(b.dataset.grade);
  });
  $("speakBtn").addEventListener("click", () => current && speak(current.word, current.lang));

  document.addEventListener("keydown", (e) => {
    if (["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName) || e.ctrlKey || e.metaKey || e.altKey) return;
    if ((e.key === " " || e.key === "Enter") && !revealed && current) { e.preventDefault(); reveal(); }
    const g = { 1: "again", 2: "hard", 3: "good", 4: "easy" }[e.key];
    if (g && revealed) { e.preventDefault(); grade(g); }
  });

  function speak(text, lang) {
    if (!("speechSynthesis" in window)) return;
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = lang === "de" ? "de-DE" : "en-US";
    u.rate = 0.9;
    speechSynthesis.speak(u);
  }

  loadBooks().then(loadQueue);
})();
