# ReaderHelper

A local, single-user EPUB reader for learning English and German. Click any word to get its
pronunciation, lemma, a short dictionary definition, a Vietnamese translation, and a Vietnamese
explanation of what the word means **in that sentence**. Save words and export them to Anki.

The Vietnamese part runs on a local model through [Ollama](https://ollama.com), so there's no API key and nothing to pay for.
Only the clicked word and its sentence are sent to the model, never the book.

## Setup

Requires Python 3.11+ (tested with 3.13 on Windows).

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m spacy download en_core_web_sm
python -m spacy download de_core_news_sm
cp .env.example .env        # optional; the defaults work without it
```

### Ollama (Vietnamese translation and explanation)

1. Install Ollama from https://ollama.com/download.
2. Pull the default model: `ollama pull qwen3:4b`
3. Make sure it's running: `ollama serve` (the desktop app starts it automatically).

`think=false` is sent so that qwen3 skips its reasoning step. This needs a **recent Ollama version**
(0.9 or newer). If the answers come back slowly or wrapped in `<think>` blocks, update Ollama.

**Switching models:** set `OLLAMA_MODEL` in `.env` (for example `OLLAMA_MODEL=qwen3:8b` or `gemma3:4b`),
run `ollama pull <model>`, then restart the server. `OLLAMA_TIMEOUT` (seconds, default 120) covers the
slow first call while the model loads.

> Windows tip: if each lookup has an extra ~2 s delay, set `OLLAMA_BASE_URL=http://127.0.0.1:11434`.
> `localhost` may try IPv6 first, and Ollama only listens on IPv4.

## Run

```bash
uvicorn backend.main:app --reload
```

Open http://localhost:8000.

- **Library (home page):** shows your books as covers, most recently opened first. Click a cover to read.
  - **Add book** (or drag an EPUB onto the page): the app reads the title, author and cover picture from
    the EPUB. A dialog lets you change the **name shown in the library**, choose your own **picture**
    (JPG/PNG/GIF/WebP, max. 5 MB) or use the **default icon**. *Cancel upload* removes the book again.
  - The ✎ button on each book edits name, author and picture, or removes the book (the EPUB file is
    deleted; saved words stay).
  - `.epub` files copied into `books/` by hand appear automatically. Cover pictures are stored in `covers/`.
- **Reader:** opens at `/read?book=<id>`; **‹** goes back to the library. It remembers your position for each book.
  - Top bar: **chapter menu** (current chapter name, click for the table of contents), **reading progress**
    in %, **A− / A+** text size, **Meaning VI / EN / DE**, auto-play audio, light/dark switch, vocabulary.
  - The book is shown as a centred page in Literata; ‹ › or the arrow keys turn pages.
  - **Two pages side by side** (open-book button next to A+): left and right page like a real book, with a
    fold in the middle; ‹ › turn two pages at once. Remembered; on narrow screens it stays one page.
    Switching keeps you at the same text.
  - The first time a book is opened, the progress takes a few seconds to appear (the app measures the book
    once and remembers it).
- **Dark mode:** the sun/moon button on every page. Without a choice, the app follows your system setting.
  The book's own colours are overridden in dark mode so the text is always readable.
- **Navigation:** use the ‹ › buttons, the ← → keys, or the Contents menu.
- **Look up a word:** click it. Close the popover with Esc or by clicking outside it.
  The first lookup can take a while because the model has to load.
- **Meaning in:** the selector in the toolbar sets the language of meanings and translations: Tiếng Việt
  (default), English or Deutsch. If it matches the book's language (e.g. a German book with meanings in
  Deutsch), you get a simpler synonym and an explanation in simple language instead of a translation.
  The setting is remembered. Saved words show their meaning language in the Notes column.
- **Translate a selection:** select text with the mouse (or Shift + arrow keys), then press the
  **Dịch · Translate** button that appears.
  - **Up to 4 words** (*gave up*, *zu Hause*, *ein wenig*) are looked up like a word: dictionary entry,
    translation, meaning in this sentence, and **Save word**, so phrases can go into your vocabulary.
  - **Longer selections** (up to about 600 characters, 3–4 sentences) get a natural Vietnamese translation
    plus up to 3 notes on difficult words. 🔊 reads the original text aloud. This takes about 10–20 s.
- **Vocabulary:** the **Vocabulary** page lists saved words. **Edit** lets you fix the Vietnamese translation,
  the explanation, or shorten the sentence (Ctrl+Enter saves, Esc cancels). This is useful when the model got
  a word wrong. **Export CSV (Anki)** downloads a file where
  front = word + sentence and back = translation + explanation. In Anki, choose *File → Import*.
  The file's header lines tell Anki that it's comma-separated and contains HTML.
- Saved words are highlighted in the text (Chrome/Edge 105+, Firefox 140+, Safari 17.2+).

## How a lookup works

1. The browser finds the clicked word and the sentence that contains it, then sends both to `POST /lookup`.
2. The server detects the language (en/de) and uses spaCy to find the base form (lemma) and the part of
   speech. German separable verbs are joined: in *"Er stand früh auf"*, both *stand* and *auf* look up *aufstehen*.
3. It fetches up to 5 dictionary senses. Senses with the wrong part of speech are dropped, and the first
   senses of every dictionary entry come before later ones, so the river sense of *bank* isn't crowded out
   by the financial ones.
4. The local model gets the word (marked `[[like this]]` in the sentence), its base form and the numbered
   senses. It picks the sense that fits (`sense_no`) and writes the Vietnamese translation, explanation and
   sense label. The answer is forced into a JSON schema.
5. The popover shows the chosen sense as the definition. **All senses** expands the full list, so you can check
   the model's choice.

| Part | Source |
| --- | --- |
| Language (en/de) | `langdetect` on the sentence; the book's language breaks ties on very short sentences |
| Lemma, part of speech, separable verbs | spaCy `en_core_web_sm` / `de_core_news_sm` |
| English senses | dictionaryapi.dev |
| German senses | English Wiktionary REST API (German entries; the definitions are written in English) |
| Vietnamese | local Ollama model |
| Audio | browser SpeechSynthesis (en-US / de-DE). Voices depend on your OS. |

**Cache:** lookups are stored in `app.db` by (language, lemma, sentence, model + prompt version). After you
switch `OLLAMA_MODEL`, words are therefore looked up fresh instead of showing the old model's answers.
Only successful model answers are cached. **↻ Ask again** in the popover skips the cache.

**Vocabulary:** saving the same word in the same sentence again updates the entry instead of duplicating it.

## Checking translation quality

`tools/eval_words.py` runs 14 tricky words (English and German, with ambiguous senses) against a running
server and counts how many Vietnamese answers contain an accepted keyword:

```bash
python tools/eval_words.py                 # default http://127.0.0.1:8000
```

Use it to compare models: change `OLLAMA_MODEL`, restart the server, run it again. With `qwen3:4b`, English
is mostly right and German often isn't (about 7 of 14). See `docs/DEVLOG.md` for details.

## Troubleshooting

| Message in popover | Fix |
| --- | --- |
| "Ollama is not running. Start it with `ollama serve`." | Start Ollama. |
| "Model '…' is not available. Run `ollama pull …`." | Pull the model, or fix `OLLAMA_MODEL`. |
| "The local model timed out…" | Click the word again (the first load is slow), or raise `OLLAMA_TIMEOUT`. |
| "The local model returned an invalid answer…" | The app already retried once. Click again, or try a larger model. |
| "Dictionary service is unreachable" / "No dictionary entry found" | Only the definition is missing; everything else still works. |
| Translation is clearly wrong | Expand **All senses** to see which sense the model chose, then press **↻ Ask again**. If this happens often, use a larger model (e.g. `qwen3:8b`). |
| Code changes don't show up | On Windows, `--reload` sometimes hangs and keeps serving old code. Stop the server (Ctrl+C) and start it again. |
