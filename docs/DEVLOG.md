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

---

## Still open (ideas)
- Test larger models with `tools/eval_words.py` (`qwen3:8b`, `gemma3:12b`) and pick a new default.
- Show the dictionary result immediately and add the Vietnamese when it arrives (lookups currently take 6–11 s).
- Look up phrases and idioms by drag-selecting several words.
- Better German lemmas (`de_core_news_md`); the small model gives e.g. *ungeheueren* → "ungeheu".
- Vocabulary: remember where in the book each word was saved and link back; built-in review mode.
- German past-tense forms like *fing* aren't lemmatized (should be *fangen*), so dictionary and translation
  miss. A larger spaCy model or a dictionary fallback for irregular verbs would help.
- Reader: font size, themes (sepia/dark), progress %, scroll mode, touch support.
- Unit tests for sentence extraction, lemmatization and JSON parsing.
