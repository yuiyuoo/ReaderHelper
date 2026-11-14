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

**Which model?** Measured with `tools/eval_words.py` (14 ambiguous English/German words), CPU only:

| Model | Correct | Time per word (CPU) |
| --- | --- | --- |
| `qwen3:4b` (default) | 7/14 – German meanings are often wrong | ~10–14 s |
| `qwen3:8b` (**recommended**, ~5 GB) | 13/14 | ~15–25 s |

If your computer has a supported graphics card, Ollama uses it automatically and both are much faster.
The dictionary part of a lookup appears after about 1–2 s either way; only the meaning takes this long.

> Windows tip: if each lookup has an extra ~2 s delay, set `OLLAMA_BASE_URL=http://127.0.0.1:11434`.
> `localhost` may try IPv6 first, and Ollama only listens on IPv4.

## Run

**Easiest (Windows):** double-click **`start.bat`**. It starts the server and shows the addresses for this PC
and for your phone. Close the window to stop it.

Or from a terminal:

```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000. (`--host 0.0.0.0` lets other devices on your Wi-Fi connect; leave it out to
allow only this PC. `--reload` restarts on code changes but sometimes hangs on Windows.)

### On your phone
1. Phone and PC on the **same Wi-Fi**, PC switched on.
2. Once, on the PC, in **PowerShell as administrator** (only for your home Wi-Fi):
   ```powershell
   Set-NetConnectionProfile -InterfaceAlias WiFi -NetworkCategory Private
   New-NetFirewallRule -DisplayName "ReaderHelper" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private
   ```
   This allows the app on trusted (private) networks only. The app has no login, so don't open it on
   public Wi-Fi.
3. Open the address `start.bat` shows (e.g. `http://192.168.31.46:8000`) in the phone's browser.
4. **Add it to the home screen** – iPhone: Safari → Share → *Add to Home Screen*; Android: Chrome → ⋮ →
   *Add to Home screen*. It gets its own icon and opens full-screen without the browser bars.

On the phone: **swipe** left/right to turn pages, tap a word for the meaning (it slides up from the
bottom), long-press to select text and press **Dịch · Translate** at the bottom of the screen. The
vocabulary list shows one card per word. The PC does all the work, so lookups are as fast as on the PC.

**Focus mode (phones):** after the first page turn the top bar disappears and only the book is on screen,
with a quiet line at the bottom (chapter · %). Tap the **margin** around the text (top, bottom, sides) or
that bottom line to bring the bar back; the next page turn hides it again. Taps on the text still look up
words. In Safari, Safari's own address bar stays (websites can't hide it on iPhone); from the home-screen
icon only the iPhone's status line (time, battery) remains.

### Away from home (Tailscale)
[Tailscale](https://tailscale.com) is a free private network between your own devices: the phone reaches
the PC from anywhere, nobody else can. (Don't open a port on your router instead – the app has no login.)

1. **PC:** install Tailscale (`winget install Tailscale.Tailscale` or from tailscale.com) and sign in.
2. **iPhone/Android:** install the Tailscale app and sign in with **the same account**; allow the VPN.
3. **PC, once,** in a terminal:
   ```bash
   tailscale serve --bg 8000
   ```
   The first time it shows a link to enable HTTPS for your Tailscale network – open it, confirm, run the
   command again. Tailscale then serves the app at `https://<pc-name>.<your-network>.ts.net`, only inside
   your Tailscale network, and remembers this setting (no firewall rule needed).
4. `start.bat` now also shows this address ("Phone, anywhere"). Open it on the phone (Tailscale switched on)
   and add it to the home screen again – it is a different address from the Wi-Fi one.

The PC still has to be on, awake and running `start.bat`.

### Sharing with friends
Each person has a **profile** with their own saved words, review schedule and highlights. The library
(books) and the AI answer cache are shared. There are no passwords: only people in your Tailscale network
can open the app at all.

1. **You:** Tailscale admin console (login.tailscale.com) → **Machines** → your PC → **⋯ → Share…** →
   copy the invite link and send it to your friend.
2. **Friend:** installs Tailscale (free, own account), opens the invite link and accepts. They only get your
   PC, none of your other devices.
3. **Friend:** opens `https://<pc-name>.<your-network>.ts.net` (the "Phone, anywhere" address), chooses
   **Add a person**, enters a name. Add to home screen as usual.

- The app asks **"Who is reading?"** the first time on each device; the choice is remembered on that device.
  The round button with the initial in the top bar switches person, adds one or renames the current one.
- Words saved before profiles existed belong to the first profile, **Me** – rename it to your own name.
- Optional, so friends can reach only the app and not other services on your PC: in the admin console under
  **Access controls**, allow shared users only port 443:
  ```json
  {"src": ["autogroup:shared"], "dst": ["<your-pc>:443"], "action": "accept"}
  ```
- Profiles keep words apart, they are not security: anyone in the network could pick someone else's name.
  Only share with people you trust.

- **Library (home page):** shows your books as covers, most recently opened first. Click a cover to read.
  - **Add book** (or drag an EPUB onto the page): the app reads the title, author and cover picture from
    the EPUB. A dialog lets you change the **name shown in the library**, choose your own **picture**
    (JPG/PNG/GIF/WebP, max. 5 MB) or use the **default icon**. *Cancel upload* removes the book again.
  - The ✎ button on each book edits name, author and picture, or removes the book (the EPUB file is
    deleted; saved words stay).
  - `.epub` files copied into `books/` by hand appear automatically. Cover pictures are stored in `covers/`.
- **Reader:** opens at `/read?book=<id>`; **‹** goes back to the library. It remembers your position for each
  book **on the server, per person**: read on the phone, open the book on the PC and it continues there (and
  the other way round). An app that is already open checks when you come back to it and jumps to where you
  read on the other device ("Continued where you last read"). Without connection the position is kept on the
  device and sent later. The library shows when *you* last read each book and how far (%).
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
  You get the base form, pronunciation and dictionary meanings right away (about 1–2 s) and can **Save
  word** straight away (the English dictionary meaning becomes the note; add a Vietnamese one later with
  Edit). **✨ Ask AI** adds the meaning in this sentence, an explanation and a translation of the whole
  sentence (15–25 s; the first time also loads the model). AI answers you already got come back instantly
  from the cache. **Selected text** (a phrase or sentences) always uses the AI (see below).
- **Grammar (German words):** under the word the popover shows
  - nouns: article and plural, with the article coloured by gender (**der** blue, **die** red, **das** green):
    *der Schlüssel, die Schlüssel, des Schlüssels*. Words with several genders (*der/die/das Band*) use the
    one in the sentence. Compound nouns missing in the dictionary take it from their last part
    (*Kinderzimmerfenster* → *Fenster*). Without any entry, only the article from the sentence is shown.
  - verbs: *er*-form · Präteritum · Perfekt, e.g. *fängt an · fing an · hat/ist angefangen*, plus
    "unregelmäßig" for irregular verbs.
  - adjectives: *größer · am größten*.

  It comes from the German Wiktionary and is saved with the word: shown on the flashcard answer, in the
  vocabulary list and on the Anki card. Words saved earlier get it when you save them again.
- **Expressions** (noun-verb combinations like *in Frage stellen*, *eine Entscheidung treffen*,
  *take into account*): tapping any part shows the whole expression, e.g. *stellte … in Frage* →
  **in Frage stellen**, also in the passive or perfect (*wurde in Frage gestellt*). They are recognised from
  the list `backend/data/expressions_de.txt` (about 160 common German ones – add your own lines) and, for
  others, from a dictionary entry. **+ Add word** in the popup combines the tapped word with another word of
  the same sentence by hand; **Just "…"** looks up only the tapped word. A saved expression is highlighted
  with all its parts wherever it occurs (*in Frage stellen* ≠ *eine Frage stellen*).
- **Meaning in:** the selector in the toolbar sets the language of meanings and translations: Tiếng Việt
  (default), English or Deutsch. If it matches the book's language (e.g. a German book with meanings in
  Deutsch), you get a simpler synonym and an explanation in simple language instead of a translation.
  With **DE** and a German word, the dictionary definitions are German too (from the German Wiktionary, e.g.
  *anfangen*: "trans.: etwas beginnen"), without the AI; with VI/EN they stay English. Fixed expressions keep
  English definitions (the German Wiktionary has few entries for them).
  The setting is remembered. Saved words show their meaning language in the Notes column.
- **Translate a selection:** select text with the mouse (or Shift + arrow keys), then press the
  **Dịch · Translate** button that appears.
  - **Up to 4 words** (*gave up*, *zu Hause*, *ein wenig*) are looked up like a word: dictionary entry,
    translation, meaning in this sentence, and **Save word**, so phrases can go into your vocabulary. Unlike a
    tapped word, the AI answer comes automatically (selecting is a deliberate choice).
  - **A whole sentence**, even a short one (*Jetzt sind wir da.*), is translated as a sentence.
  - **Longer selections** (up to about 600 characters, 3–4 sentences) get a natural Vietnamese translation
    plus up to 3 notes on difficult words. 🔊 reads the original text aloud. This takes about 10–20 s.
    Each note has **+ Save**: the word goes into your vocabulary with the note as its meaning, its base form,
    grammar and the sentence it stands in (no second AI call).
- **Vocabulary:** the **Vocabulary** page lists saved words. **Edit** lets you fix the Vietnamese translation,
  the explanation, or shorten the sentence (Ctrl+Enter saves, Esc cancels). This is useful when the model got
  a word wrong. **Export CSV (Anki)** downloads a file where
  front = word + sentence and back = translation + explanation. In Anki, choose *File → Import*.
  The file's header lines tell Anki that it's comma-separated and contains HTML.
  - Each word remembers the **book and exact place** it was saved from: the book link under the word
    opens the book there and briefly marks the word in yellow. Filter the list by book with *Words from*.
- **Review** (button on the library and vocabulary pages, with the number of words waiting): flashcards.
  You see the word in its sentence, try to remember the meaning, press **Show answer** (Space), then rate
  yourself: **Again** (comes back in 10 min, in this session) · **Hard** · **Good** · **Easy** (keys 1–4).
  The buttons show when the word will come back; words you know come back less and less often
  (1 day → 3 days → about a week → …), forgotten ones start over. Filter by book with *Words from*.
  - **Fill in the gap** (switch at the top right, remembered): the sentence is shown with a gap and the
    meaning you saved; type the word as it stands in the book (*fing*, not *fangen*) and press Enter.
    Separated words get one gap per part (*fing … an*, *stellte … in Frage*). **Hint** shows the first
    letter, a second time the base form. Typos and *ue* for *ü* count as "almost"; capitals don't matter.
    The app then suggests a rating (right → Good, with hint or almost → Hard, wrong → Again); Enter takes
    it. Words without a saved meaning are shown as normal cards.
- Saved words are highlighted in the text (Chrome/Edge 105+, Firefox 140+, Safari 17.2+). The marker
  button in the reader's top bar turns that on or off.

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
python tools/eval_words.py -v              # also print the model's translation of each sentence
```

Use it to compare models: change `OLLAMA_MODEL`, restart the server, run it again (results above).
See `docs/DEVLOG.md` for details.

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
