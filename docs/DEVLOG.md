# Development log

What went wrong while building ReaderHelper, why, and how it was fixed. The newest work is at the bottom.
The format is **Problem → Cause → Fix**, with the files involved.

---

## Part 1: Initial build

### 1. spaCy failed to import on the first try
- **Problem:** `ImportError: DLL load failed … An Application Control policy has blocked this file.`
- **Cause:** Windows Smart App Control checks unknown binaries the first time they load. It blocked one of
  spaCy's compiled modules once.
- **Fix:** Nothing in the code. The same import worked on the next try. The project uses a
  **Python 3.13** venv because spaCy's prebuilt packages are most reliable there (the machine's default is 3.14).

### 2. Garbled instruction in the spec
- **Problem:** CLAUDE.md says to strip "any `<details>…Denkprozess…` block".
- **Cause:** a rendering glitch in the spec. The intended tag is `<think>…</think>`, the reasoning block
  that qwen3 can output.
- **Fix:** `parse_response` removes `<think>…</think>` blocks and code fences before reading the JSON.
  *File:* `backend/llm.py`

### 3. Sentences cut off at line ends
- **Problem:** clicking *tired* in Alice sent only "Alice was … on the bank, and" instead of the full sentence.
- **Cause:** Gutenberg EPUBs contain hard line breaks inside paragraphs. The browser's sentence splitter
  (`Intl.Segmenter`) treats a newline as the end of a sentence.
- **Fix:** before splitting, replace every whitespace character with a single space. The replacement is one
  character for one character, so the click position inside the text stays valid.
  *File:* `frontend/reader.js` (`sentenceAt`)

### 4. German definitions missing (Wiktionary 403)
- **Problem:** German words often had no definition.
- **Cause:** Wikimedia rejected requests with a generic User-Agent: `403 Please respect our robot policy`.
  The app also treated that error as "word not found".
- **Fix:** send a User-Agent of the form Wikimedia accepts (app name, a URL, the library name). Responses
  403, 429 and 5xx now mean "dictionary unreachable", not "no entry".
  *File:* `backend/nlp.py`

### 5. CSS code inside German definitions
- **Problem:** definitions contained text like `.mw-parser-output .object-usage-tag{font-style:italic}`.
- **Cause:** Wiktionary's HTML includes inline `<style>` blocks. Removing only the tags left their CSS as text.
- **Fix:** remove `<style>` and `<script>` blocks completely before stripping the remaining tags.
  *File:* `backend/nlp.py` (`_strip_html`)

### 6. "Ollama not running" reported as a timeout
- **Problem:** with Ollama stopped and a short timeout, the popover said "timed out".
- **Cause:** on Windows, a refused connection is retried for about 2 s, which can exceed the overall timeout.
- **Fix:** give the connection its own timeout, separate from the time the model may take to answer.
  *File:* `backend/llm.py`

### 7. About 2 s extra per request to `localhost`
- **Problem:** requests to `localhost` were noticeably slow.
- **Cause:** Windows tries IPv6 (`::1`) first, and the servers only listen on IPv4.
- **Fix:** tip in the README to set `OLLAMA_BASE_URL=http://127.0.0.1:11434`. The default stays as the spec says.

### 8. Status bar clipped at the bottom
- **Problem:** the footer showing book and section was half hidden.
- **Cause:** the reader area had `height: 100%` plus a margin, so it was taller than its container.
- **Fix:** use padding on the container instead of a margin on the viewer. *File:* `frontend/style.css`

### Testing without Ollama
Ollama wasn't installed at first, so the model step was tested against a **fake Ollama server**. It covered
valid JSON, `<think>` and code-fence wrapping, invalid output followed by a working retry, Chinese
characters, a missing model, a timeout, and Ollama being down. The test data was removed from `app.db`
afterwards.

---

## Part 2: First real use with Ollama

### 9. "Ollama is not running" and `ollama: command not found`
- **Problem:** every lookup showed the warning. Typing `ollama` in the terminal failed.
- **Cause:** Ollama wasn't installed at first. After installing it, the terminal that was already open
  didn't have the new PATH yet.
- **Fix:** open a new terminal (or call `%LOCALAPPDATA%\Programs\Ollama\ollama.exe` directly), then run
  `ollama pull qwen3:4b`. No code change. The warning was correct, and the definition still showed
  because the dictionary doesn't depend on Ollama.

### 10. Wrong translation: *lag* → "vỏ" (shell)
Three causes stacked on top of each other:

| Cause | Fix | File |
| --- | --- | --- |
| The browser still used the **old cached `reader.js`** (from before fix #3), so the model only saw the fragment "Er lag auf seinem panzerartig harten Rücken". | The server sends `Cache-Control: no-cache` for the HTML/JS/CSS, so browsers always check for a new version. | `backend/main.py` |
| The prompt didn't mark **which** word to explain, so the model translated *Panzer/Rücken*. It also copied the example's wording ("nơi người ta có thể ngồi"). | The target word is marked `[[lag]]` in the sentence, its base form (*liegen*) is included, and the example is new and labelled "format only, do not copy". | `backend/llm.py` |
| The wrong answer was **cached**, so clicking again showed it again. | New **↻ Ask again** button, which sends `refresh: true` to skip the cache. | `frontend/reader.js`, `backend/main.py` |

### 11. Server kept running old code
- **Problem:** after editing `llm.py`, answers still came from the old prompt.
- **Cause:** `uvicorn --reload` on Windows logged "Reloading…", but the old worker process never stopped.
- **Fix:** restart the server by hand. Noted under Troubleshooting in the README.

### 12. Tried and reverted: first dictionary definition as a hint
- **Idea:** give the model the dictionary definition so it knows the meaning.
- **Result:** it fixed *Rücken*, but broke *bank* (river), which turned into "ngân hàng". The dictionary
  lists the most common sense first, and the small model simply copied it.
- **Decision:** reverted. This led to fix #14 (give *all* senses and let the model choose).

---

## Part 3: Improvements

### 13. Changing the model kept showing old answers
- **Problem:** the cache key was (language, lemma, sentence). After switching to another model, every word
  you had already looked up still showed the old model's answer.
- **Fix:** the key now also includes a `variant` = model name + `PROMPT_VERSION`. When the app starts, it
  detects the old cache table and rebuilds it. Only the cache is dropped; saved vocabulary is kept.
  *Files:* `backend/db.py`, `backend/llm.py` (`cache_variant`, `PROMPT_VERSION`)

### 14. Model picks among dictionary senses
- **Problem:** small models guess the meaning and often pick the wrong one.
- **Fix:**
  - The dictionary is now queried **before** the model, and up to 5 numbered senses go into the prompt.
    The model returns `sense_no` together with the Vietnamese fields.
  - Senses with the wrong part of speech are dropped (spaCy says *lag* is a VERB, so noun senses go).
  - Each dictionary entry contributes its first senses before any entry contributes more. Without this,
    *bank* (river) never appeared: dictionaryapi.dev lists 8 or more financial senses first.
  - The popover shows the chosen sense as the definition ("sense 2 of 5"), with the full list under
    **All senses**.

  *Files:* `backend/nlp.py` (`fetch_senses`, `_select`, `POS_NAMES`), `backend/llm.py`, `backend/main.py`,
  `frontend/reader.js`, `frontend/style.css`

### 15. Model echoed the input instead of answering
- **Problem:** after the senses were added, all 14 test words failed. The model returned
  `{"Language": "English", "Target word": "bank", "Dictionary senses": [...]}`.
- **Cause:** `format: "json"` only forces *some* valid JSON. With a longer, structured prompt, the 4B model
  turned the input itself into JSON.
- **Fix:** send a **JSON schema** as `format` (Ollama's structured outputs), so the model can only produce
  `sense_no`, `translation_vi`, `gloss_vi` and `sense_vi`. The prompt also ends with an explicit
  "Answer (JSON for the target word …)" line. *File:* `backend/llm.py` (`answer_schema`)

### 16. Too many senses confused the model
- **Problem:** with 8 senses, *lag* got sense 4 ("to be located") → "địa điểm".
- **Fix:** at most 5 senses (`MAX_SENSES`). The effect was small; see the results below.

### 17. German separable verbs
- **Problem:** in *"Er stand früh auf"*, clicking *stand* looked up *stehen* (to stand), but the meaning is
  *aufstehen* (to get up).
- **Fix:** spaCy marks the separated particle with the dependency label `svp`. Clicking the verb or the
  particle now gives the lemma particle + verb → *aufstehen*. It also works for *sah … an* → *ansehen* and
  *macht … zu* → *zumachen*. *File:* `backend/nlp.py` (`lemmatize`)

### 18. Duplicate vocabulary entries
- **Problem:** saving the same word twice created two rows.
- **Fix:** saving the same lemma in the same sentence updates the existing row. *File:* `backend/db.py`

### 19. Measuring quality instead of guessing
`tools/eval_words.py` checks 14 tricky words against a running server. Results with **qwen3:4b**
(a case passes if the Vietnamese answer contains an accepted keyword):

| Version | Passed | Notes |
| --- | --- | --- |
| Before Part 3 (word marking only) | 8/14 | about 4–5 s per word |
| Sense list (8 senses) + `format: "json"` | 0/14 | model echoed the input (#15) |
| Sense list + JSON schema | 6/14 | about 6–11 s per word |
| Sense list (5 senses) + JSON schema (**current**) | 7/14 | about 6–11 s per word |

**What the numbers mean:**
- The keyword check is strict. Some "fails" are correct, e.g. *light* → "không nặng" (not heavy) or
  *aufstehen* → "đứng lên" (stand up). Some "passes" are only partly right, e.g. *Schloss* → "chìa khóa"
  (key instead of lock) passed in the baseline.
- **English** is mostly right in both versions. *runs a company* went from "chạy" (run on foot) to
  "quản lý" (manage).
- **German** is still often wrong: *lag*, *kaum*, *Ungeziefer*, *Schloss*. The model now gets the right
  sense in its list and still picks a wrong one.
- **Conclusion:** the pipeline is no longer the bottleneck; **qwen3:4b's German → Vietnamese ability is**.
  The sense list should pay off with a stronger model (next step: `qwen3:8b`), and it already lets you
  check the model's choice yourself.
- **Cost:** a lookup takes about 2–4 s longer than before, because the dictionary now runs before the model
  instead of at the same time, and the prompt is longer. Cached lookups are still instant.

---

## Part 4: Translating selected text

### 20. New feature: select text → translate
- **Goal:** select a phrase or a few sentences and get a Vietnamese translation.
- **Design decisions:**
  - **A button instead of automatic translation.** A small **Dịch · Translate** button appears after
    selecting. Translating every selection automatically would waste 10–20 s of model time whenever you
    only select text to re-read it.
  - **Two modes by length.** Selections of up to 4 words without sentence punctuation are treated as a
    phrase and go through the normal word lookup (`/lookup`, with the surrounding sentence as context).
    That gives dictionary senses, the in-context meaning and **Save word** for idioms. Longer selections go
    to the new `POST /translate`, which returns a full translation plus up to 3 notes.
  - **Limit of 600 characters** (about 3–4 sentences). This keeps the "never send the whole book" rule
    and keeps answers fast. Longer selections get a clear message instead of being cut silently.
    The browser checks this first, and the server checks it again.
- **Backend changes:**
  - `lemmatize` handles multi-word selections. In English, each word is lemmatized ("gave up" →
    "give up"). German phrases are kept as written, because word-by-word lemmas mangle them
    ("zu Hause" → "zu Haus").
  - Wiktionary page titles need `_` instead of spaces ("zu_Hause").
  - The prompt says "target phrase" instead of "target word" for multi-word selections. Single-word prompts
    are unchanged, so the word results from Part 3 still apply.
  - Passages have their own prompt and JSON schema (`translation_vi` and `notes[{term, explanation_vi}]`),
    a token limit of 1000 instead of 400, and share the cache table under the placeholder lemma `[passage]`.

  *Files:* `backend/main.py` (`/translate`), `backend/llm.py` (`translate_passage`, `PASSAGE_SCHEMA`),
  `backend/nlp.py`, `frontend/reader.js`, `frontend/index.html`, `frontend/style.css`

### 21. Notes that only repeat the translation
- **Problem:** some notes were whole sentences, e.g. *"Was ist mit mir geschehen?" → "Điều gì đã xảy ra
  với tôi?"*. That just repeats the translation.
- **Fix:** drop notes whose term is longer than 4 words, and notes whose term doesn't appear in the
  selected text (small models sometimes invent terms).

### 22. Translate button came back after being pressed
- **Problem:** after pressing the button, the popover opened but the button appeared again.
- **Cause:** the button hides itself on mouse-down. The mouse-up then lands on the still-selected text
  in the book, which fires the "selection finished" handler again.
- **Fix:** ignore selection events for 600 ms after the button is pressed. *File:* `frontend/reader.js`

### Results (qwen3:4b)

| Selection | Result | Verdict |
| --- | --- | --- |
| *Er lag auf seinem panzerartig harten Rücken und sah, …* | "Anh ấy **nằm ngửa trên lưng** cứng như panzer và nhìn thấy bụng uốn cong, màu nâu…" | Mostly right; "panzer" left untranslated |
| *»Was ist mit mir geschehen?« dachte er. Es war kein Traum.* | "Anh ấy đang suy nghĩ: 'Điều gì đã xảy ra với tôi?' và đó không phải là giấc mơ." | Right |
| *Alice was beginning to get very tired of sitting by her sister on the bank…* | "Alice bắt đầu cảm thấy rất mệt mỏi vì phải ngồi bên em gái trên bờ sông…" | Right, except "em gái" (should be "chị gái") |
| *There was nothing so very remarkable in that; …* | "Điều đó không có gì đặc biệt; Alice cũng không nghĩ rằng…" | Right |
| *gave up* / *zu Hause* / *ein wenig* (phrases) | từ bỏ / tại nhà / một chút | All right |

**Interesting:** whole-sentence translation is clearly **better** than single-word lookup with the same
model. *lag* inside the sentence became "nằm" (correct); as a single word it was wrong every time.
Translating a sentence is a more natural task for a language model than picking one sense of one word.
Notes are useful about half the time; the rest are trivial.

**Speed:** 8–16 s per passage after the model has loaded (the first call took 110 s because the model
had to load).

---

## Part 5: Editing saved words

### 23. Saved words couldn't be corrected
- **Problem:** wrong model answers ended up in the vocabulary and the Anki export. Example from real use:
  *"Die Katze **fing** die Maus"* was saved as "ngón tay" (finger), although *fing* is a form of *fangen*
  (to catch). The only option was to delete the word.
- **Fix:**
  - New endpoint `PATCH /api/vocab/{id}`. It changes only the fields that are sent, and only
    `translation_vi`, `gloss_vi` and `sentence` are allowed (a whitelist). Text is trimmed and normalised
    to NFC. An empty sentence is rejected (400); an unknown id returns 404.
  - Vocabulary page: **Edit** turns the row into input fields with Save and Cancel. Ctrl+Enter saves and
    Esc cancels. Errors appear inside the row.
  - Word and lemma can't be edited, because they come from the book. Fix a wrong lemma by deleting the
    entry and saving the word again.
- **Tested:** partial update, empty sentence, unknown id, editing in the browser, persistence after reload,
  Esc, Ctrl+Enter, and the CSV export containing the edited text.

  *Files:* `backend/db.py` (`update_vocab`), `backend/main.py`, `frontend/vocab.html`, `frontend/style.css`

---

## Part 6: Choosing the meaning language (vi / en / de)

### 24. Meanings only in Vietnamese
- **Request:** choose whether meanings and translations are in Vietnamese, English or German.
- **Fix:**
  - Toolbar selector **Meaning in** (Tiếng Việt / English / Deutsch), remembered in localStorage. It is
    sent as `target` to `/lookup` and `/translate`; the server only accepts `vi`, `en` or `de` (422 otherwise).
  - **Prompt:** the model now sees neutral keys (`translation`, `gloss`, `sense`, `explanation`) instead of
    `translation_vi`. Asking for English text under a key named `…_vi` would confuse a small model.
    The API responses keep the original field names from the spec (`translation_vi`, …) plus a new
    `target_lang` field, so the frontend and vocabulary didn't need renaming.
  - **One example per target language** (bờ sông / riverbank / Ufer). Small models copy the language of
    the example, so a Vietnamese example would make them answer in Vietnamese.
  - **Same language as the book** (e.g. German book, meaning in German): the prompt asks for a simpler
    synonym and a simple explanation (learner's-dictionary style); passages are rewritten in simpler language.
  - **Cache:** the target language is part of the cache variant, so switching languages never shows
    answers in the old language.
  - **Vocabulary:** new column `target_lang`, added automatically to existing databases (existing words
    became `vi`). Saving the same word with another meaning language creates a separate entry.
    The Notes column shows the language as a small tag.
  - Popover labels follow the language of the answer (e.g. "Meaning in this sentence",
    "Bedeutung in diesem Satz").
- **Results (qwen3:4b):** all answers came back in the chosen language; no Vietnamese leaked into en/de.
  The quality is the same as before: *bank* (river) was right in all three languages, *lag* was wrong in all
  three, and the German passage translated *bank* as "Bank" instead of "Ufer". German → German explanations
  were weak (once the model just copied the sentence).

  *Files:* `backend/llm.py`, `backend/main.py`, `backend/db.py`, `frontend/index.html`,
  `frontend/reader.js`, `frontend/vocab.html`

---

## Part 7: Library home page

### 25. Books were only a file-name dropdown
- **Request:** a home page showing the books; when uploading, give the book a display name and, if
  possible, a picture, otherwise a default icon. Ideas came from the design canvas
  "ReaderHelper UI" (reader screens only, no home page). Its palette, book font and 44 px controls
  were taken over for the whole app.
- **Fix:**
  - New `books` table (file name, title, author, language, cover, added/opened time) and
    `backend/books.py`, which reads title, author, language and the **embedded cover** straight from
    the EPUB. EPUB 3 `cover-image`, EPUB 2 `<meta name="cover">`, or an image named "cover" all work,
    so most books get a picture without uploading one.
  - Library API: list (syncs with `books/`: hand-copied files appear, missing files disappear),
    upload, rename (`PATCH`), cover upload/remove, "opened" timestamp, delete.
  - **Home page** (`/`): cover grid sorted by last opened, language tag, "Opened 2 days ago",
    ✎ edit button, "Add book" card, drag & drop, empty state. Books without a picture get a coloured
    tile with a book icon and the title; the colour is derived from the title, so it stays the same.
  - **Add/edit dialog:** name (prefilled from the EPUB), author, picture preview. The picture is only
    uploaded on Save, so Cancel really cancels; *Cancel upload* removes a just-added book again.
  - **Reader** moved to `/read?book=<id>` with a "‹ Library" link and the book's name. Reading
    positions are still stored per file name, so positions from before the library are kept.
  - Safety: uploaded pictures are checked by their content (not the file name), max. 5 MB; uploads
    never overwrite another book's file; book and cover paths can't escape their folders. Cover files
    get a new name on every change, so browsers can cache them forever without showing old pictures.

### 26. Drop hint covered the whole page
- **Problem:** the "Drop the EPUB to add it" overlay was always visible.
- **Cause:** a CSS rule `display: flex` overrides the browser's built-in `[hidden] { display: none }`.
- **Fix:** a global `[hidden] { display: none !important; }`, so this can't happen to any element.

- **Tested in the browser:** upload → prefilled dialog (title and author from the EPUB) → rename → default
  icon → add; edit → own picture; cancel upload; open book → reader → back ("Opened just now", first in
  the list); remove book; unknown book id; phone width. API: non-EPUB upload, non-image cover, empty
  title, path tricks → all rejected.

  *Files:* `backend/books.py` (new), `backend/db.py`, `backend/main.py`, `frontend/library.html` (new),
  `frontend/library.js` (new), `frontend/index.html`, `frontend/reader.js`, `frontend/style.css`, `.gitignore`

---

## Part 8: New reading view + dark mode

### 27. Reading view redesign
- **Request:** a reading view like the user's mock-up (slim top bar with chapter menu, progress %, A−/A+,
  warm paper background, centred page in a serif font) plus dark mode.
- **Fix:**
  - Top bar: back to library, **chapter menu** (current chapter as its label, table of contents as a
    dropdown with the current entry highlighted), **progress** (% + bar), **A−/A+** (70–170 %, remembered),
    **Meaning VI/EN/DE** as a segmented switch, auto-play as a toggle button, light/dark switch,
    vocabulary with a count badge.
  - The book sits in a centred "page card" (max. 820 px); text in Literata, line height 1.7. Messages
    appear as a short toast instead of a permanent status line.
  - **Progress** needs epub.js "locations" (positions spread through the whole book). Generating them
    reads the book once, which takes a few seconds for long books, so they are cached per book in
    localStorage and generated after the first page is already visible.
  - The palette changed to the warm paper colours of the mock-up; every colour is now a CSS variable.
  *Files:* `frontend/index.html`, `frontend/reader.js`, `frontend/style.css`

### 28. Dark mode
- `frontend/theme.js` (loaded in `<head>` so there is no flash of the wrong theme) sets `data-theme` on
  every page. It follows the system setting until the user picks light or dark with the sun/moon button.
  The choice is remembered.
- A second set of colour variables for `[data-theme="dark"]`. All hard-coded colours (`#fff` buttons,
  input borders, dashed borders…) were replaced by variables so nothing stays light.
- **Inside the book** (an iframe), the app injects its own stylesheet and toggles a class `rh-dark`. In dark
  mode it overrides the book's own text and background colours, which are often hard-coded black/white.

### 29. Problems found while testing the new view
| Problem | Cause | Fix |
| --- | --- | --- |
| "Opening book…" stayed on the page and pushed the book out of the card | epub.js adds its view to the container without clearing it | Clear the container before rendering |
| Chapter label stuck on "1. …" after jumping to chapter 2 | Several chapters share one file (separated by `#anchors`); the code only compared files | Compare each chapter anchor's position with the end of the shown page |
| Book stayed white in dark mode, even with a transparent background | When the page uses `color-scheme: dark` and the iframe doesn't, the browser paints an opaque white backdrop behind the iframe | Give the book's document the same `color-scheme` |
| epub.js themes not used | Switching epub.js themes leaves the old theme's rules in the document | Own stylesheet + class toggle |
| Vocabulary page said "Back to reader" but went to the library | Leftover label | Renamed to "← Library" |

- **Tested in the browser:** chapter label + menu (88 entries, jump updates label and progress), progress
  (1 % → 6 %), text size, meaning switch, dark mode in page and book (text colour checked), theme kept
  after reload, library and vocabulary in dark mode, phone width. No script errors.

---

## Part 9: Two pages side by side

### 30. Book-like double page
- **Request:** optionally show two pages next to each other, like an open book.
- **Fix:** an open-book button in the top bar switches epub.js between `spread: "none"` and
  `spread: "always"`. The page card widens (up to 1440 px) and gets a soft fold shadow down the middle
  (light and dark variants). epub.js itself falls back to one page below 760 px, and the button is hidden
  on phones. The setting is remembered.
  *Files:* `frontend/index.html`, `frontend/reader.js`, `frontend/style.css`

### 31. Keeping the reading position (when switching, and on every reload)
Testing "does the same text stay on screen?" uncovered several position bugs. One of them (reload) had
existed since the beginning; the earlier tests only checked the section, not the exact page.

| Problem | Cause | Fix |
| --- | --- | --- |
| Switching one/two pages jumped to the start of the section | Re-layouting in place makes epub.js go to the section start and save that position | Rebuild the view and open it at the saved text |
| Reload opened one or two pages too early | epub.js computes the page before the book's web font (Literata) has loaded; the font changes line breaks, so the text moves to later pages | Wait until the font stylesheet has loaded and Literata is usable (`document.fonts.load`), then jump again. `fonts.ready` alone isn't enough: it resolves before the font download has even started |
| Still one page too early sometimes | A saved position is the first character of a page, exactly on a page boundary, and epub.js sometimes rounds it down to the page before | After jumping, check whether the position is really on screen; if not, turn forward (max. 3 pages) |
| That check said "visible" when it wasn't | A saved position is an empty range, which has no on-screen box | Measure the first character instead |
| Wrong position used when switching | `rendition.currentLocation()` can return a stale page | Use the position from the last `relocated` event |
| One → two → one pages ended one page earlier | A spread starts at its left page, which is the page before the one you were reading | Remember the original text while switching; forget it as soon as you turn a page yourself (it can't be tied to the `relocated` event, which epub.js reports with a delay) |

- **Tested in the browser** (twice in a row): reload opens exactly the same page; the text read in
  one-page mode is visible after switching to two pages; switching back returns to exactly the same
  page. Page turning moves two pages, words on the right page can be clicked (popover opens), dark mode
  works, and the earlier UI tests still pass.

---

## Part 10: Better and faster meanings

### 32. Wrong base forms for irregular German verbs
- **Problem:** *fing* (from *fangen*) was looked up as "fing" and translated as "ngón tay" (finger).
  The same happened with *molk* (*melken*), *anfing* (*anfangen*) and *aß* (*essen*).
- **Cause:** the small German spaCy model doesn't know many irregular forms and returns the word unchanged.
- **Fix:** if spaCy returns the word unchanged, use the base form from `simplemma` (a small word-list
  lemmatizer, new dependency). German only: for English, spaCy is good, and simplemma made "tired"
  into "tire". Works for separable verbs too (*fing … an* → *anfangen*).
  *File:* `backend/nlp.py` (`_base_form`)

### 33. Sentence first, then the word
- **Idea:** translating whole sentences had worked better than single words (Part 4). So the model now
  writes a translation of the whole sentence first (`sentence`, the first key in the JSON schema, so it
  really comes first) and then has to use the same word for `translation`. The popover shows that
  sentence translation in italics under the original sentence.
- **Same language as the book** (e.g. German → German): the sentence is rewritten in simpler language.

### 34. Measuring models and prompts
`tools/eval_words.py` (14 ambiguous words, CPU only, no graphics card):

| | Old prompt | New prompt (sentence first) |
| --- | --- | --- |
| `qwen3:4b` | 8/14 · ~10 s per word | 7/14 · ~14 s |
| `qwen3:8b` | 11/14 · ~11 s | **13/14** · ~20 s |

- **Most of the gain comes from the larger model.** The new prompt adds two more correct words with
  qwen3:8b (*fire* → "sa thải", *stand … auf* → "đứng dậy"), but costs ~8 s more on a CPU because the
  answer is longer. With qwen3:4b it doesn't help: the small model already translates the German
  sentence wrongly (*Bank* → "trường", *Schloss* → "chuyến đi").
- With qwen3:8b, the cases that always failed before are now right: *lag* → "nằm", *Rücken* → "lưng",
  *kaum* → "gần như không", *Bank* → "ghế dài", *Schloss* → "ổ khóa". The one remaining "fail",
  *Ungeziefer* → "sinh vật đáng khinh" (contemptible creature), is close in meaning.
- **Decision:** the code default stays `qwen3:4b` (as in the spec). This machine uses `qwen3:8b` through the
  local `.env`, and the README recommends it with the measured numbers.

### 35. Speed: show what's ready, load the model early
- **Two steps:** the reader first calls `/lookup` with `quick: true`. That returns base form, language,
  dictionary senses and the sentence (no model). The popover shows it with "Finding the meaning in this
  sentence…", and pronunciation works right away. Then the reader asks again without `quick` for the
  model's answer. A cached word comes back complete in the first step.
- The dictionary result is kept in a small in-memory cache (1000 entries), so the second step doesn't
  fetch it again. Network errors aren't cached.
- **Warm-up:** opening a book calls `POST /api/warmup`. That loads the Ollama model (a request without a
  prompt only loads it) and both spaCy models in the background.
- **Measured in the browser** (qwen3:8b, CPU):

| | Before | After |
| --- | --- | --- |
| Something useful on screen | after the whole lookup (~15–30 s) | **~2 s** (dictionary, base form, audio) |
| First lookup after opening a book | + model loading time | model already loaded by the warm-up |
| Quick step, first word | — | 5 s at first, because spaCy loaded on first use → also warmed up now → 1.9 s |
| Same word again | 0.1 s (cache) | 0.1 s |

  *Files:* `backend/main.py` (`quick`, `/api/warmup`), `backend/llm.py` (`warmup`, prompt v8),
  `backend/nlp.py` (sense cache), `frontend/reader.js`, `frontend/style.css`, `tools/eval_words.py` (`-v`)

### Still slow
On this machine everything runs on the CPU. A graphics card supported by Ollama would make the model
answer several times faster; the app needs no change for that.

---

## Part 11: Learning – review mode and words per book

### 36. Saved words were only an archive
- **Goal:** help to actually remember the words, and connect each word with the place it came from.
- **Review mode** (`/review`, new `backend/srs.py`): flashcards with spaced repetition, a simplified SM-2
  (the idea behind Anki).
  - The front shows the word in its sentence (word marked), language, book, 🔊. **Show answer** (Space)
    reveals the translation, the explanation and the sentence translation.
  - Four answers (keys 1–4). Each button shows when the word will come back:

    | Answer | Next time | Effect |
    | --- | --- | --- |
    | Again | 10 min (again in this session) | starts over, word becomes "harder" |
    | Hard | ~1.2 × last interval (min. 1 day) | slightly harder |
    | Good | 1 day → 3 days → interval × ease (≈ 2.5) | |
    | Easy | 4 days, then grows faster | slightly easier |

  - Order: words that are due first (most overdue first), then new words (oldest saved first). The
    session can be limited to one book.
- **Words per book:** saving a word now also stores the book, the exact position (EPUB CFI of the word or
  selected phrase) and the translation of the whole sentence. The vocabulary page shows the book under
  each word with a link; **Open in book** opens the reader at that position and marks the word in yellow
  for 4 s. Afterwards the position is removed from the address, so a reload continues where you read.
  Both pages can be filtered by book; the library shows "N words" per book and a Review button with the
  number of words waiting.
- **Highlight on/off:** a marker button in the reader's top bar (remembered).
- **Database:** new `vocab` columns (`sentence_tr`, `book_id`, `cfi`, `due_at`, `interval_days`, `ease`,
  `reps`, `lapses`, `reviewed_at`), added automatically to existing databases. Removing a book keeps its
  words (the link is cleared).
- **Existing words:** the 4 words saved before this change were linked to their book once, by finding their
  sentence in each book's text. Their exact position is unknown, so their link opens the book at the
  current reading position ("Open the book →").
- **Tested in the browser on a copy of the data** (so the real review schedule wasn't touched): saving
  stores book + position + sentence translation; highlight toggle; book filter; "Open in book" lands on the
  word and marks it; a full review session (Again → card returns at the end → 6 answers for 5 cards →
  "next word due tomorrow"); library and vocabulary badges; phone width.

  *Files:* `backend/srs.py` (new), `backend/db.py`, `backend/main.py`, `frontend/review.html` + `review.js`
  (new), `frontend/reader.js`, `frontend/index.html`, `frontend/vocab.html`, `frontend/library.html`,
  `frontend/library.js`, `frontend/style.css`

---

## Part 12: Phone access and phone polish

### 37. The phone (and other devices) couldn't open the app
| Problem | Cause | Fix |
| --- | --- | --- |
| Connection refused from other devices | The server listened on `127.0.0.1` (this PC only) | Start with `--host 0.0.0.0` (`start.bat` does that) |
| Still blocked | Wi-Fi set to *Public*; the existing firewall rules allowed Python **3.14**, but the app runs on Python **3.13** (`.venv`) | User set the home Wi-Fi to *Private* and allowed port 8000 on private networks only (admin PowerShell, in the README). Not on public networks, because the app has no login |

### 38. Phone polish
- **Home-screen app:** `manifest.json`, app icons (192/512, maskable, Apple touch icon; drawn as SVG and
  rendered to PNG), `apple-mobile-web-app-*` and `theme-color` tags on every page, `viewport-fit=cover` with
  safe-area padding so nothing sits under the notch or home indicator. From the home screen it opens
  full-screen; every page has its own way back, so no browser back button is needed.
  (No service worker yet: browsers only allow it over https, i.e. after setting up e.g. Tailscale.)
- **Swipe to turn pages** in the reader: only a quick (< 0.8 s), clearly horizontal move of at least
  50 px counts, so tapping words and long-pressing to select text still work.
- **Bottom sheet:** on screens up to 600 px the word/translation popup slides up from the bottom, full
  width, max. 70 % of the screen, with a handle. Save / Ask again stay fixed at its bottom while the content
  scrolls.
- **Translate button** for selections sits at the bottom centre on phones, away from the system's own
  copy/paste menu above the selection.
- **Reader top bar** in two rows instead of three (91 px instead of ~140 px on an iPhone 13).
- **Vocabulary list** as one card per word instead of a wide table.
- **`start.bat`:** double-click to start the server for phone access; it prints the PC and phone addresses.

### 39. Swiping right left the reader
- **Problem:** in the phone test, swiping right went back to the library instead of the previous page.
- **Cause:** the browser treats a horizontal drag as its "go back" gesture. `overscroll-behavior-x: none`
  wasn't enough, because the swipe happens inside the book's iframe.
- **Fix:** for a clearly horizontal drag the reader cancels the browser's default (`touchmove`,
  non-passive), except while a text selection is being adjusted. (On an iPhone, the system's edge-swipe from
  the very edge of the screen can't be blocked by any web page; swipes that start inside the page are fine.)

- **Tested** with iPhone 13 emulation (touch): manifest and icon served; swipe forward/back; a tiny swipe is
  ignored; tapping a word opens the bottom sheet (full width, at the bottom); tapping outside closes it;
  Translate button at the bottom centre; vocabulary without sideways scrolling. Desktop tests still pass.
  Not tested on a real iPhone.

  *Files:* `frontend/manifest.json`, `frontend/icons/` (new), all four HTML pages, `frontend/reader.js`,
  `frontend/style.css`, `start.bat` (new)

### 40. Tapping a word did nothing on a real iPhone
- **Problem:** on the phone, tapping a word showed nothing. In the PC browser's phone mode it worked.
- **Cause:** on an iPhone every browser uses Apple's engine (WebKit), and it sends **no `click` event** for a
  tap on plain text (only on links, buttons and other "clickable" elements). The reader looked words up on
  `click`, so the tap never arrived. The PC's phone mode is still Chrome's engine, which does send the click,
  so the problem didn't show there. The same quirk affected "tap outside closes the popup" (`mousedown`).
- **Fix:** the reader recognises a tap itself from the touch events (finger moved < 10 px, touch < 0.6 s) and
  looks the word up directly. A click that some browsers (Android) still send afterwards is ignored for
  0.8 s, so a word is never looked up twice. "Tap outside" now uses `pointerdown`, which fires on every device.
- **Tested** by imitating the iPhone in the phone emulation: all click events inside the book are blocked
  before they reach the app → tapping a word still opens the popup. Without blocking (Android-like) the
  word is looked up exactly once. Earlier phone and desktop tests still pass.
  *File:* `frontend/reader.js`

### 41. Still nothing on the iPhone: Safari delivers no events into the book
- **Problem:** after #40, tapping a word still did nothing in Safari on the iPhone.
- **Cause:** epub.js shows each chapter in an iframe with `sandbox="allow-same-origin"`, i.e. scripts disabled.
  Chrome still delivers taps and clicks inside it to the reader's listeners (so every test passed), but
  Safari/WebKit delivers **no events at all** into such a frame: no taps, no swipes, no selection. A known
  epub.js issue on iPhone/iPad; WebKit itself couldn't be run on this Windows PC to reproduce it.
- **Fix:** epub.js option `allowScriptedContent: true` (sandbox becomes `allow-same-origin allow-scripts`).
  So that book files still can't run code, an epub.js hook rewrites each chapter before it is shown:
  `<script>` elements become inert `<script type="text/plain">` (kept as elements, so saved positions don't
  shift) and `on…=""` event attributes are renamed.
- **Also:** the highlighting of saved words used a regex lookbehind `(?<!…)`, which iPhones before iOS 16.4
  don't support; rewritten without it (whole words only, same result).
- **Tested** (Chrome engine): sandbox attribute, script neutralising (element count unchanged, normal text
  untouched), highlighting on a story chapter, tap/swipe/bottom-sheet tests, reading-position tests. **Not
  verified on a real iPhone** – that needs the user's test.
  *File:* `frontend/reader.js`

---

## Part 13: AI on/off

### 42. Sometimes the dictionary is enough
- **Request:** a switch to turn the AI off; with the dictionary alone a word should still be saveable.
- **Fix** (`frontend/reader.js`, `frontend/index.html`, `frontend/style.css`, `backend/main.py`):
  - ✨ **AI** button in the reader's top bar (remembered). Off: a lookup stops after the fast first step
    (base form, dictionary, audio) and the popup says "AI is off – dictionary only".
  - **Save word** works without an AI answer: the chosen English dictionary meaning is stored as the note
    (`target_lang` = en); a Vietnamese meaning can be added later with Edit. The same applies when the AI
    step failed.
  - **✨ Ask AI** in the popup gets the AI answer for just this word (or passage) without turning the AI on.
  - Selected passages need the AI; with the AI off the popup says so and offers Ask AI.
  - Answers already in the cache are shown complete even with the AI off.
  - With the AI off, opening a book only preloads spaCy (`/api/warmup?ai=false`), not the language model
    (about 6 GB of memory saved).
- **Found while testing:**
  - With the AI off, the first lookup took 4.5 s because nothing was preloaded, not even spaCy. Now spaCy is
    always preloaded → 0.4 s.
  - Wiktionary answered `429 Too Many Requests` after the quick series of test lookups, so the dictionary
    looked "unreachable". Dictionary requests now wait 1.5 s and retry once on 429.
- **Tested on a copy of the data:** AI off → no preloading of the model, popup after 0.4 s with only the
  dictionary request; saving without AI; Ask AI (answer after ~14–19 s); passage with AI off (message, no
  request); switching AI on preloads the model and is remembered.

### 43. Saved "fangen" also highlighted "fing … an" (anfangen)
- **Problem:** after saving *fing* (fangen), every *fing* in the book was highlighted, including
  *Da fing sie an zu weinen*, where it belongs to **anfangen**.
- **Cause 1:** highlighting compared only the **spelling** of saved words, not their base form.
- **Cause 2:** the small German spaCy model often misses separable verbs when words come between verb and
  particle (*fing das Mädchen an*: dependency `op` instead of `svp`). So even clicking *fing* there gave
  *fangen*, and the word was saved with the wrong base form and therefore a wrong meaning.
- **Fix:**
  - Highlighting by **base form**: `POST /api/highlights` gets the chapter's paragraphs and returns where
    words occur whose base form was saved, using `token_lemma()`, the same function a clicked word goes
    through. A saved *fangen* now marks *fing, fängt* but not *fing … an*; a saved *anfangen* marks both
    parts. Paragraph analyses are cached in memory (first analysis of a 70,000-character chapter ≈ 2 s,
    afterwards ≈ 1 ms, so re-highlighting after saving a word is instant). Phrases are still matched by
    spelling in the browser; so is everything if the server can't be reached.
  - **Separable verbs:** spaCy's tagger still tags the particle `PTKVZ` even when the parser misses the
    link. Such a particle now belongs to the nearest finite verb before it in the sentence, if particle +
    verb is a known German word (`simplemma.is_known`: *anfangen* ✓, *aufkatze* ✗). *Das Buch lag auf dem
    Tisch* stays *liegen* (there `auf` is a preposition, not `PTKVZ`).
  - A trap on the way: spaCy creates a new Token object on every access, so comparing tokens with `is`
    never matched; tokens are now compared by position.
  - **Base forms:** spaCy sometimes invents a non-word (*fortwarfst* → "fortwarfsen"). If spaCy's result
    isn't a known German word but simplemma's is, simplemma's is used (*fortwerfen*).
  - The user's saved words were re-lemmatised once from their sentences (backup taken): `fing` → *fangen*,
    the second `fing` (from *fing sie an*) → *anfangen*, `fortwarfst` → *fortwerfen*. Their saved meanings
    were generated with the wrong base form and are left for the user to redo (Ask again → Save word).
- **Tested:** unit checks for fangen/anfangen/ansehen/aufstehen/liegen and English; in the browser, the
  chapter "Jakobs Haus": all four *fing* = fangen highlighted, *fing sie an* not, one request per chapter.
  *Files:* `backend/nlp.py` (`token_lemma`, `find_lemmas`, separable-verb helpers, `_base_form`),
  `backend/main.py` (`/api/highlights`), `frontend/reader.js` (`highlightSaved`)

### 44. Does the separable-verb fix work for all verbs, or only for "fangen"?
- **Question:** is #43 a fix for the one example or general?
- **General:** no word lists. It relies on spaCy's tagger (`PTKVZ` = separated particle) and on simplemma's
  dictionary to confirm that particle + verb is a real word.
- **Measured** with 37 sentences (`tools/check_lemmas.py`, new): many different separable verbs
  (*ankommen, einkaufen, mitnehmen, zurückkommen, aussehen, festhalten, teilnehmen, stattfinden,
  fernsehen …*), unseparated forms (*aufsteht, angekommen*) and traps where the particle is really a
  preposition (*lag auf dem Tisch, sah aus dem Fenster, ging mit ihrem Bruder*). First run: 20/22.
- **Two gaps found and fixed:**
  - **Verb first** (imperative, yes/no question): spaCy tags a capitalised verb at the start of a sentence
    as a name/noun (*Mach die Tür zu!*, *Stehst du früh auf?*). The first word of a sentence is now also tried
    as the verb (simplemma on the lowercased word, or stem + "en" for imperatives like *Ruf/Komm*).
  - A verb form must also **look** like an infinitive (lowercase, ends in -n): simplemma read *Ruf* as the
    noun "der Ruf", and "Anruf" is a real word, so *Ruf … an* became "anRuf" instead of *anrufen*.
- **Result: 36/37.** Still wrong: irregular 2nd person with vowel change at the start of a question
  (*Nimmst du den Schirm mit?*): neither spaCy nor simplemma know *nimmst*. The same verb elsewhere
  (*Sie nahm ihn mit*) works. Base forms and English were re-checked: unchanged.
  *Files:* `backend/nlp.py`, `tools/check_lemmas.py` (new)

## Where the app differs from the original spec (CLAUDE.md)

| Spec | Now | Why |
| --- | --- | --- |
| Model input: language, word, sentence only | + base form, + up to 5 dictionary senses; the word is marked `[[ ]]` | Small models picked the wrong word or sense (#10, #14) |
| Model returns only the three Vietnamese fields | + `sense_no` | So the popover can show the matching definition (#14) |
| `format="json"` | JSON schema (structured outputs) | Plain JSON mode let the model echo the input (#15) |
| Cache key (lang, lemma, sentence) | + model/prompt version | Otherwise old answers survive a model change (#13) |
| Dictionary and model in any order | Dictionary first, then model | The model needs the senses (#14) |
| `/lookup` takes `{word, sentence}` | + optional `lang_hint`, `refresh` | Short-sentence language detection; ↻ Ask again |
| Only single words are looked up | + phrases (≤ 4 words) through `/lookup`; + passages through new `POST /translate` (≤ 600 chars) | Feature request (#20) |
| Meanings always in Vietnamese | Selectable: Vietnamese (default), English, German; `*_vi` fields hold the chosen language, `target_lang` says which | Feature request (#24) |
| Model returns translation, gloss, sense | + `sentence` (translation of the whole sentence, written first) | Better word meanings with qwen3:8b (#33, #34) |
| One `/lookup` call | Optional `quick` step first (dictionary only), then the model | Something useful on screen after ~2 s (#35) |
| Vocabulary: save, list, delete, CSV | + edit, book + position, review scheduling (`/review`, `/api/review`) | Learning features (#23, #36) |

---

## Still open (ideas)
- Edit a word directly from the review card; show review statistics (words learned per week).
- Access from outside the home Wi-Fi (Tailscale), then a service worker so the app starts offline and Review works without the PC.
- Book footnote numbers in chapter names ("Die drei Schläfer.(2)").
- German adjective forms the lemmatizers miss (*ungeheueren* → should be *ungeheuer*).
- Scroll mode, search in the book.
- Automated tests in the repo (pytest + one browser smoke test); split `reader.js` into modules.
