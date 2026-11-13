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

  // "card": see the word, remember the meaning. "cloze": see the meaning, type the missing word.
  const MODE_KEY = "reviewMode";
  let mode = (() => { try { return localStorage.getItem(MODE_KEY); } catch { return null; } })() === "cloze" ? "cloze" : "card";
  let cloze = null;  // current card in cloze mode: {gaps: [{start, end, answer}], hints, checked}

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
    renderCounts();
    if (!current) return showDone();
    show(current);
  }

  function show(c) {
    revealed = false;
    $("done").hidden = true;
    $("card").hidden = false;
    $("cLang").textContent = c.lang;
    $("cBook").textContent = c.book_title ? `from ${c.book_title}` : "";
    $("cWord").textContent = c.word;
    $("cLemma").textContent = c.lemma && c.lemma.toLowerCase() !== c.word.toLowerCase() ? `base form: ${c.lemma}` : "";
    $("cGrammar").innerHTML = grammarHtml(c.grammar);
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
    $("grades").querySelectorAll("button").forEach((b) => b.classList.remove("suggested"));
    $("fcHint").hidden = false;
    $("cFeedback").hidden = true;
    cloze = mode === "cloze" ? startCloze(c) : null;
    $("wordRow").hidden = $("cLemma").hidden = !!cloze;
    $("cPrompt").hidden = $("clozeActions").hidden = $("keysCloze").hidden = !cloze;
    $("showBtn").hidden = $("keysCard").hidden = !!cloze;
    if (cloze) {
      $("fcHint").textContent = "Which word fits the gap?";
      $("cSentence").querySelector("input.gap").focus();
    } else {
      $("fcHint").textContent = mode === "cloze"
        ? "This one is shown as a card (no meaning saved, or the word isn't in the sentence)."
        : "Do you remember what it means here?";
      $("showBtn").focus();
    }
  }

  function reveal() {
    if (!current || revealed) return;
    revealed = true;
    $("answer").hidden = false;
    $("grades").hidden = false;
    $("showBtn").hidden = true;
    $("fcHint").hidden = true;
    $("wordRow").hidden = $("cLemma").hidden = false;
    $("grades").querySelector(".g-good").focus();
  }

  // ---------- fill in the gap ----------

  const escRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  /** Where the saved word stands in the sentence. Separated words ("fing … an", "stellte … in Frage")
      become one gap per part, in order. null if a part can't be found. */
  function findGaps(sentence, word) {
    const parts = word.split(/\s*…\s*|\s+/).filter(Boolean);
    const low = sentence.toLowerCase();
    const gaps = [];
    let from = 0;
    for (const part of parts) {
      // Whole words only ("an" must not match inside "kann"); a group instead of lookbehind for older iPhones.
      const m = new RegExp(`(^|[^\\p{L}])(${escRe(part.toLowerCase())})(?![\\p{L}])`, "u").exec(low.slice(from));
      if (!m) return null;
      const start = from + m.index + m[1].length;
      gaps.push({ start, end: start + part.length, answer: sentence.slice(start, start + part.length) });
      from = start + part.length;
    }
    return gaps.length ? gaps : null;
  }

  /** The sentence with gaps (inputs) or, after checking, with the results. */
  function clozeHtml(sentence, gaps, results) {
    let html = "", at = 0;
    gaps.forEach((g, i) => {
      html += esc(sentence.slice(at, g.start));
      if (!results) {
        const w = Math.max(3, g.answer.length) + 1.5;
        html += `<input class="gap" data-i="${i}" style="width:${w}ch" autocomplete="off" autocapitalize="off"
          autocorrect="off" spellcheck="false" enterkeyhint="done" aria-label="Missing word${gaps.length > 1 ? ` ${i + 1}` : ""}">`;
      } else {
        const r = results[i];
        const typed = r.verdict === "right" || !r.typed ? "" : `<s>${esc(r.typed)}</s>`;
        html += `<span class="gap-res ${r.verdict}">${typed}${esc(g.answer)}</span>`;
      }
      at = g.end;
    });
    return html + esc(sentence.slice(at));
  }

  function startCloze(c) {
    const meaning = c.translation_vi || c.gloss_vi;
    const gaps = meaning ? findGaps(c.sentence, c.word) : null;
    if (!gaps) return null;  // nothing to ask with, or nowhere to put the gap: show it as a normal card
    $("cSentence").innerHTML = clozeHtml(c.sentence, gaps);
    $("cPrompt").innerHTML = `<span class="label">Meaning</span><b>${esc(c.translation_vi || "")}</b>` +
      (c.translation_vi && c.gloss_vi ? " – " : "") + esc(c.gloss_vi ? c.gloss_vi.slice(0, 160) : "");
    $("hintBtn").disabled = false;
    return { gaps, hints: 0, checked: false };
  }

  function hint() {
    if (!cloze || cloze.checked) return;
    cloze.hints += 1;
    const inputs = [...$("cSentence").querySelectorAll("input.gap")];
    const c = current;
    if (cloze.hints === 1) {
      inputs.forEach((el, i) => { el.placeholder = cloze.gaps[i].answer.slice(0, 1) + "…"; });
    } else {
      // Second hint: the base form (tells you which word; you still need the right form), else more letters.
      if (c.lemma && c.lemma.toLowerCase() !== c.word.toLowerCase()) {
        $("cLemma").textContent = `base form: ${c.lemma}`;
        $("cLemma").hidden = false;
      } else {
        inputs.forEach((el, i) => {
          const a = cloze.gaps[i].answer;
          el.placeholder = a.slice(0, Math.ceil(a.length / 2)) + "…";
        });
      }
      $("hintBtn").disabled = true;
    }
    (inputs.find((el) => !el.value) || inputs[0])?.focus();
  }

  function levenshtein(a, b) {
    const d = Array.from({ length: b.length + 1 }, (_, j) => j);
    for (let i = 1; i <= a.length; i++) {
      let prev = d[0];
      d[0] = i;
      for (let j = 1; j <= b.length; j++) {
        const tmp = d[j];
        d[j] = Math.min(d[j] + 1, d[j - 1] + 1, prev + (a[i - 1] === b[j - 1] ? 0 : 1));
        prev = tmp;
      }
    }
    return d[b.length];
  }

  /** right / almost (umlaut written as ae/oe/ue/ss, or one typo) / wrong. Capitals don't count. */
  function judge(typed, answer) {
    const norm = (s) => s.normalize("NFC").trim().toLowerCase().replace(/[.,;:!?"'«»„“”‚‘’()]/g, "");
    const t = norm(typed), a = norm(answer);
    if (!t) return "wrong";
    if (t === a) return "right";
    const plain = (s) => s.replace(/ä/g, "ae").replace(/ö/g, "oe").replace(/ü/g, "ue").replace(/ß/g, "ss");
    if (plain(t) === plain(a) || (a.length >= 4 && levenshtein(t, a) <= 1)) return "almost";
    return "wrong";
  }

  function check() {
    if (!cloze || cloze.checked) return;
    cloze.checked = true;
    const inputs = [...$("cSentence").querySelectorAll("input.gap")];
    const results = cloze.gaps.map((g, i) => {
      const typed = inputs[i]?.value || "";
      return { typed, verdict: judge(typed, g.answer) };
    });
    const verdicts = results.map((r) => r.verdict);
    const overall = verdicts.includes("wrong") ? "wrong" : verdicts.includes("almost") ? "almost" : "right";
    $("cSentence").innerHTML = clozeHtml(current.sentence, cloze.gaps, results);
    const fb = $("cFeedback");
    fb.className = `fc-feedback ${overall}`;
    fb.textContent = { right: cloze.hints ? "Right – with a hint." : "Right!",
                       almost: "Almost – check the spelling.", wrong: "Not quite." }[overall];
    fb.hidden = false;
    $("clozeActions").hidden = true;
    reveal();
    // Suggest a grade; Enter takes it, or pick another.
    const g = overall === "wrong" ? "again" : overall === "almost" || cloze.hints ? "hard" : "good";
    const btn = $("grades").querySelector(`[data-grade="${g}"]`);
    btn.classList.add("suggested");
    btn.focus();
  }

  function setMode(m) {
    mode = m;
    try { localStorage.setItem(MODE_KEY, m); } catch { /* ignore */ }
    $("modeGroup").querySelectorAll("[data-mode]").forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.mode === m)));
    if (current) show(current);  // same card again, in the new mode
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
  $("checkBtn").addEventListener("click", check);
  $("hintBtn").addEventListener("click", hint);
  $("modeGroup").addEventListener("click", (e) => {
    const b = e.target.closest("[data-mode]");
    if (b && b.dataset.mode !== mode) setMode(b.dataset.mode);
  });
  $("cSentence").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.matches("input.gap")) { e.preventDefault(); check(); }
  });
  $("grades").addEventListener("click", (e) => {
    const b = e.target.closest("[data-grade]");
    if (b) grade(b.dataset.grade);
  });
  $("speakBtn").addEventListener("click", () => current && speak(current.word, current.lang));

  document.addEventListener("keydown", (e) => {
    if (["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName) || e.ctrlKey || e.metaKey || e.altKey) return;
    if (cloze && !cloze.checked) {
      if (e.key === "Enter") { e.preventDefault(); check(); }
      return;
    }
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

  setMode(mode);
  loadBooks().then(loadQueue);
})();
