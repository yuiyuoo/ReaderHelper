Build a personal-use MVP web app: an EPUB reader for language learning. Run locally, single user, no auth. No paid APIs: the AI part runs locally via Ollama.

## Goal
I read EPUB books in English or German. When I click a word, a popover shows:
1. Pronunciation audio (button + auto-play optional)
2. Lemma and short source-language definition
3. Vietnamese translation
4. Vietnamese explanation of the word's meaning IN THE CURRENT SENTENCE (not just the default sense)
5. "Save word" button

## Tech stack
- Frontend: plain HTML + vanilla JS (no build step) + EPUB.js (CDN), served by the backend
- Backend: Python 3.11+, FastAPI, uvicorn
- NLP: spaCy (`en_core_web_sm`, `de_core_news_sm`) for lemma + sentence splitting
- Language detection: `langdetect` per sentence, collapsed to "en" or "de"
- Dictionary: English via https://api.dictionaryapi.dev ; German via Wiktionary REST API (graceful fallback to empty)
- Vietnamese translation + context gloss: local LLM via Ollama (free, no API key). Default model `qwen3:4b`, configurable in `.env` via `OLLAMA_MODEL`. Also `OLLAMA_BASE_URL` (default `http://localhost:11434`) and `OLLAMA_TIMEOUT` (seconds, default 120). I install Ollama and pull the model myself; the app only calls its HTTP API.
- Audio: browser SpeechSynthesis (en-US / de-DE), rate 0.9
- Storage: SQLite (file `app.db`)

## Features
1. Upload an EPUB (or place in `/books`) and read it in the browser with next/prev navigation and remembered position (localStorage).
2. On click of a word inside the EPUB iframe: determine the clicked word via caretRangeFromPoint / selection expansion (not by requiring manual text selection), extract the exact containing SENTENCE (not the whole paragraph), and call `POST /lookup` with `{word, sentence}`.
3. `/lookup`: detect language, lemmatize with correct spaCy model, fetch definition, call the LLM, return JSON:
   `{lang, word, lemma, definition, translation_vi, gloss_vi, sense_vi, warning}`
   - `translation_vi`: short Vietnamese equivalent of the word as used in THIS sentence
   - `gloss_vi`: Vietnamese explanation of the word's meaning in THIS sentence
   - `sense_vi`: short Vietnamese label of the chosen sense (max 6 words)
   - `warning`: null normally; a friendly message if the LLM step failed (the three Vietnamese fields are then empty strings, but lemma and definition are still returned)
4. LLM call (Ollama, in `backend/llm.py`):
   - `POST {OLLAMA_BASE_URL}/api/generate` with `stream=false`, `format="json"`, `think=false`, `keep_alive="30m"`, options `temperature=0.2` and `num_predict=400`, and the configured timeout (the first call is slow while the model loads).
   - Input to the model: language, word, sentence only. The model returns only `{translation_vi, gloss_vi, sense_vi}`; the backend adds the other fields.
   - Prompt: act as a Vietnamese tutor for a B1 learner; reply ONLY as JSON; use the sense that fits THIS sentence; `gloss_vi` max 25 words; set `gloss_vi` to "—" for proper nouns/trivial words; write all three fields in Vietnamese only (no Chinese characters). Keep the prompt short and include one tiny example of the expected JSON, because small local models follow examples better.
   - Parse JSON robustly: strip code fences and any `
<details class="___1dvsyi5 f1qtf1oa fp9bwmr f1iv7wbt f1hq7n4j f1dyikfo f1aup2by f1scgt6o f1duaf1 f1m8eeeo">
  <summary>Denkprozess</summary>
  

...
</details>` block, extract the first `{...}`, validate the keys, retry once if the JSON is invalid. Normalize Vietnamese output to Unicode NFC.
5. Cache lookups in SQLite keyed by (lang, lemma, sentence); return cached results instantly. Cache only successful LLM results; never cache a response that has a warning.
6. Vocabulary: table `vocab` (id, lang, word, lemma, sentence, translation_vi, gloss_vi, created_at). Endpoints to save, list, delete. A simple `/vocab` page listing saved words, with CSV export (Anki-compatible: front = word + sentence, back = translation + gloss).
7. Popover UI: positioned near the click, closes on outside click/Esc, shows loading state (the first lookup can take a while), shows `warning` text if present, handles errors. Cancel the previous in-flight request when another word is clicked. Use a font with Vietnamese support (Noto Sans / Be Vietnam Pro). UTF-8 everywhere.
8. Highlight saved words in the text (optional, if simple).

## Project structure
- `backend/main.py`, `backend/db.py`, `backend/nlp.py`, `backend/llm.py`
- `frontend/index.html`, `frontend/reader.js`, `frontend/style.css`, `frontend/vocab.html`
- `requirements.txt`, `.env.example` (OLLAMA_* variables only, no API keys)
- `README.md`: setup + run steps, including `python -m spacy download en_core_web_sm`, `python -m spacy download de_core_news_sm`, installing Ollama, `ollama pull qwen3:4b`, how to switch models via `OLLAMA_MODEL`, and a note that `think=false` needs a recent Ollama version.

## Constraints
- Keep it simple: no frameworks beyond those listed, no over-engineering. Small helper libraries are fine (httpx, python-dotenv, python-multipart).
- Never send the whole book to the LLM; only word + sentence.
- Handle errors gracefully with friendly messages: Ollama not running ("Start it with `ollama serve`"), model not pulled ("Run `ollama pull <model>`"), timeout, invalid JSON from the model, dictionary API down, unknown word. A failure in one source must not break the others.
- Add brief comments only where logic is non-obvious.

## Deliverable
Working app runnable with `uvicorn backend.main:app --reload`, then open http://localhost:8000. After building, test with a public-domain Gutenberg EPUB (one English, one German), plus one run with Ollama stopped to check the fallback, and tell me what works and what doesn't. If Ollama or internet access isn't available in your environment, say so and tell me exactly what you could not verify.

Work step by step: first scaffold + EPUB rendering, then click→sentence extraction, then backend lookup, then LLM (Ollama), then vocab. Pause and summarize after each step.
