"""Translation + in-context gloss (Vietnamese, English or German) via a local Ollama model."""
import json
import logging
import os
import re
import unicodedata

import httpx

log = logging.getLogger(__name__)

# The model sees neutral key names; responses keep the API names (translation_vi, ...) from the spec,
# but their content is in the chosen target language.
MODEL_KEYS = ("translation", "gloss", "sense")
OUT_KEYS = {"translation": "translation_vi", "gloss": "gloss_vi", "sense": "sense_vi"}
LANG_NAMES = {"en": "English", "de": "German", "vi": "Vietnamese"}
TARGETS = ("vi", "en", "de")
CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]")

# One example per target language: small models copy the example's language, so it must match.
WORD_EXAMPLE = {  # "banks" in "The children played on the banks of the river."
    "vi": ("bờ sông", "Phần đất ở hai bên mép sông.", "bờ (sông, hồ)"),
    "en": ("riverbank", "The land along the edge of a river.", "edge of a river"),
    "de": ("Ufer", "Das Land am Rand eines Flusses.", "Rand eines Gewässers"),
}
SENTENCE_EXAMPLE = {  # the same example sentence, translated
    "vi": "Bọn trẻ chơi đùa trên bờ sông.",
    "en": "The children played on the riverbank.",
    "de": "Die Kinder spielten am Ufer des Flusses.",
}
PASSAGE_EXAMPLE = {  # "She couldn't make up her mind, so she stayed at home."
    "vi": ("Cô ấy không thể quyết định được, nên cô ở nhà.", "quyết định, đưa ra lựa chọn"),
    "en": ("She couldn't decide, so she stayed home.", "to decide, to choose"),
    "de": ("Sie konnte sich nicht entscheiden, also blieb sie zu Hause.", "sich entscheiden"),
}


class LLMError(Exception):
    """Carries a user-facing message."""


def _config() -> tuple[str, str, float]:
    return (
        os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/"),
        os.getenv("OLLAMA_MODEL", "qwen3:4b"),
        float(os.getenv("OLLAMA_TIMEOUT", "120")),
    )


def mark_word(word: str, sentence: str) -> str:
    """Wrap the first whole-word occurrence in [[ ]] so small models know which word to explain."""
    m = re.search(rf"(?<!\w){re.escape(word)}(?!\w)", sentence)
    return f"{sentence[:m.start()]}[[{m.group(0)}]]{sentence[m.end():]}" if m else sentence


# Bump when the prompt changes meaningfully: it is part of the cache key, so old answers are not reused.
PROMPT_VERSION = 8


def cache_variant(target: str = "vi") -> str:
    """Identifies who produced an answer (model + prompt + target language), used in the cache key."""
    return f"{_config()[1]}|p{PROMPT_VERSION}|{target}"


def _tutor_intro(target: str) -> str:
    return (f"You are a language tutor for a B1 learner whose language is {LANG_NAMES[target]}. "
            "Reply ONLY with JSON.\n")


def build_prompt(lang: str, word: str, sentence: str, lemma: str | None = None,
                 senses: list[str] | None = None, target: str = "vi") -> str:
    T = LANG_NAMES[target]
    same = target == lang  # e.g. German book, meaning in German: learner's-dictionary style
    ex_tr, ex_gloss, ex_sense = WORD_EXAMPLE[target]
    base = f" (base form: {lemma})" if lemma and lemma.lower() != word.lower() else ""
    sense_rules = (
        '"sense_no" (number of the dictionary sense that fits THIS sentence; 0 if none fits), '
        if senses else ""
    )
    query_senses = (
        "Dictionary senses (English):\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(senses, 1)) + "\n"
        if senses else ""
    )
    # Multi-word selections are called "phrase"; single-word prompts stay exactly as before.
    kind = "phrase" if " " in word.strip() else "word"
    equivalent = (f"a simpler {T} word or synonym for the target {kind}" if same
                  else f"short {T} equivalent of the target {kind}")
    return (
        _tutor_intro(target)
        + f"Task: explain ONE target {kind}, marked with [[ ]] in the sentence, exactly as it is used there. "
        + f"The word fields are about the target {kind} only, not the other words of the sentence.\n"
        + (f"Step 1: rewrite the whole sentence in simpler {T}.\n" if same
           else f"Step 1: translate the whole sentence into natural {T}.\n")
        + ("Step 2: read the numbered dictionary senses and pick the one that matches your sentence "
           "(often NOT the first one).\n" if senses else "")
        + f"Step {3 if senses else 2}: explain the target {kind} with the meaning it has in your sentence; "
        f'"translation" must be the {T} word you used for it there.\n'
        + f'Keys: "sentence" (step 1), {sense_rules}'
        f'"translation" ({equivalent}), '
        f'"gloss" (simple {T} explanation of what the target {kind} means in this sentence, max 25 words; '
        'use "—" for proper nouns or trivial words), '
        f'"sense" (short {T} label of this sense, max 6 words).\n'
        f"Write all text values in {T} only. No Chinese characters.\n\n"
        "Example (shows the format only, do not copy its content):\n"
        "Language: English\nTarget word: banks (base form: bank)\n"
        + ("Dictionary senses (English):\n1. (noun) An institution where one can place and borrow money.\n"
           "2. (noun) An edge of river, lake, or other watercourse.\n" if senses else "")
        + "Sentence: The children played on the [[banks]] of the river.\n"
        + "{" + json.dumps({"sentence": SENTENCE_EXAMPLE[target]}, ensure_ascii=False)[1:-1]
        + (', "sense_no": 2, ' if senses else ", ")
        + json.dumps({"translation": ex_tr, "gloss": ex_gloss, "sense": ex_sense}, ensure_ascii=False)[1:]
        + "\n\n"
        f"Language: {LANG_NAMES.get(lang, lang)}\n"
        f"Target {kind}: {word}{base}\n"
        f"{query_senses}"
        f"Sentence: {mark_word(word, sentence)}\n"
        f"Answer (JSON for the target {kind} \"{word}\"):\n"
    )


def parse_response(text: str, n_senses: int = 0) -> dict:
    """Extract and validate the JSON object. Raises ValueError when unusable.
    Adds "sense_no" (int, or None if missing/out of range) when senses were offered."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"```(?:json)?", "", text)
    m = re.search(r"\{.*?\}", text, flags=re.S)
    if not m:
        raise ValueError("no JSON object")
    # Non-greedy match may stop at a '}' inside a string; fall back to the widest object.
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.S)
        data = json.loads(m.group(0)) if m else None
    if not isinstance(data, dict) or not all(isinstance(data.get(k), str) for k in MODEL_KEYS):
        raise ValueError("missing keys")
    out = {OUT_KEYS[k]: unicodedata.normalize("NFC", data[k].strip()) for k in MODEL_KEYS}
    # Translation of the whole sentence (asked for first, so the word fits it).
    out["sentence_tr"] = unicodedata.normalize("NFC", str(data.get("sentence") or "").strip())
    if not out["translation_vi"]:
        raise ValueError("empty translation")
    if any(CJK.search(v) for v in out.values()):
        raise ValueError("contains Chinese characters")
    if n_senses:
        try:
            n = int(data.get("sense_no"))
        except (TypeError, ValueError):
            n = None
        out["sense_no"] = n if n is not None and 1 <= n <= n_senses else None
    return out


def answer_schema(with_sense: bool) -> dict:
    """JSON schema for Ollama structured outputs: the model can only produce these keys
    (plain format="json" let small models echo the input back as JSON)."""
    # Key order matters: the model writes the sentence translation first and the word to match it.
    props = {"sentence": {"type": "string"}}
    if with_sense:
        props["sense_no"] = {"type": "integer"}
    props.update({k: {"type": "string"} for k in MODEL_KEYS})
    return {"type": "object", "properties": props, "required": list(props)}


async def _generate(client: httpx.AsyncClient, base: str, model: str, prompt: str,
                    schema: dict | str = "json", num_predict: int = 400) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": schema,
        "think": False,
        "keep_alive": "30m",
        "options": {"temperature": 0.2, "num_predict": num_predict},
    }
    try:
        r = await client.post(f"{base}/api/generate", json=payload)
    except httpx.ConnectError:
        raise LLMError("Ollama is not running. Start it with `ollama serve`.")
    except httpx.TimeoutException:
        raise LLMError("The local model timed out (the first call can be slow while it loads). Try again.")
    except httpx.HTTPError as e:
        raise LLMError(f"Could not reach Ollama: {e}")
    if r.status_code == 404 or (r.status_code >= 400 and "not found" in r.text.lower()):
        raise LLMError(f"Model '{model}' is not available. Run `ollama pull {model}`.")
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", r.text)
        except ValueError:
            msg = r.text
        raise LLMError(f"Ollama error: {msg[:200]}")
    return r.json().get("response", "")


_warming = False


async def warmup() -> None:
    """Ask Ollama to load the model now (a request without a prompt only loads it). Errors are ignored:
    if Ollama isn't running, the real lookup reports that properly."""
    global _warming
    if _warming:
        return
    _warming = True
    base, model, timeout = _config()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
            await client.post(f"{base}/api/generate", json={"model": model, "keep_alive": "30m"})
    except httpx.HTTPError as e:
        log.info("model warm-up skipped: %s", e)
    finally:
        _warming = False


async def explain(lang: str, word: str, sentence: str, lemma: str | None = None,
                  senses: list[str] | None = None, target: str = "vi") -> dict:
    """Return {translation_vi, gloss_vi, sense_vi[, sense_no]} (text in the target language).
    Raises LLMError with a friendly message."""
    base, model, timeout = _config()
    prompt = build_prompt(lang, word, sentence, lemma, senses, target)
    # Separate connect timeout so "Ollama not running" is reported as such, not as a slow model.
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(10.0, timeout + 5))) as client:
        for attempt in (1, 2):  # retry once on unusable output
            # More tokens than before: the answer now also contains the translated sentence.
            raw = await _generate(client, base, model, prompt, answer_schema(bool(senses)), num_predict=700)
            try:
                return parse_response(raw, len(senses or []))
            except (ValueError, json.JSONDecodeError) as e:
                log.warning("LLM attempt %d returned invalid JSON (%s): %r", attempt, e, raw[:300])
    raise LLMError("The local model returned an invalid answer. Try clicking the word again.")


# ---------- passage translation (selected sentences) ----------

PASSAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "translation": {"type": "string"},
        "notes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"term": {"type": "string"}, "explanation": {"type": "string"}},
                "required": ["term", "explanation"],
            },
        },
    },
    "required": ["translation", "notes"],
}


def build_passage_prompt(lang: str, text: str, target: str = "vi") -> str:
    T = LANG_NAMES[target]
    ex_tr, ex_note = PASSAGE_EXAMPLE[target]
    task = (
        f"Task: rewrite the {T} text in simpler, clear {T} for a learner (keep the meaning).\n"
        if target == lang else
        f"Task: translate the {LANG_NAMES.get(lang, lang)} text into natural, fluent {T} "
        "(keep the meaning and tone; do not translate word by word).\n"
    )
    return (
        _tutor_intro(target)
        + task
        + f'Keys: "translation" (the full {T} version), '
        '"notes" (up to 3 difficult words or expressions copied exactly from the text, each with '
        f'"term" and "explanation": a short {T} explanation, max 15 words; [] if nothing is difficult).\n'
        f"Write {T} only. No Chinese characters.\n\n"
        "Example (shows the format only, do not copy its content):\n"
        "Text: She couldn't make up her mind, so she stayed at home.\n"
        + json.dumps({"translation": ex_tr, "notes": [{"term": "make up her mind", "explanation": ex_note}]},
                     ensure_ascii=False)
        + f"\n\nText: {text}\n"
        "Answer (JSON):\n"
    )


def parse_passage(raw: str, text: str) -> dict:
    data = json.loads(re.sub(r"<think>.*?</think>", "", raw, flags=re.S).strip().strip("`"))
    tr = unicodedata.normalize("NFC", str(data.get("translation", "")).strip())
    if not tr:
        raise ValueError("empty translation")
    notes = []
    for n in data.get("notes") or []:
        if not isinstance(n, dict):
            continue
        term, expl = str(n.get("term", "")).strip(), str(n.get("explanation", "")).strip()
        # Drop notes whose term isn't really in the text (small models invent some), and "notes"
        # that are whole sentences: they just repeat the translation.
        if term and expl and term.lower() in text.lower() and len(term.split()) <= 4:
            notes.append({"term": term, "explanation_vi": unicodedata.normalize("NFC", expl)})
    if CJK.search(tr) or any(CJK.search(n["explanation_vi"]) for n in notes):
        raise ValueError("contains Chinese characters")
    return {"translation_vi": tr, "notes": notes[:3]}


async def translate_passage(lang: str, text: str, target: str = "vi") -> dict:
    """Return {translation_vi, notes:[{term, explanation_vi}]} (text in the target language).
    Raises LLMError with a friendly message."""
    base, model, timeout = _config()
    prompt = build_passage_prompt(lang, text, target)
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(10.0, timeout + 5))) as client:
        for attempt in (1, 2):
            # A few sentences in Vietnamese plus notes need more tokens than a single word.
            raw = await _generate(client, base, model, prompt, PASSAGE_SCHEMA, num_predict=1000)
            try:
                return parse_passage(raw, text)
            except (ValueError, json.JSONDecodeError) as e:
                log.warning("Passage attempt %d invalid (%s): %r", attempt, e, raw[:300])
    raise LLMError("The local model returned an invalid answer. Try again.")
