"""Vietnamese translation + in-context gloss via a local Ollama model."""
import json
import logging
import os
import re
import unicodedata

import httpx

log = logging.getLogger(__name__)

KEYS = ("translation_vi", "gloss_vi", "sense_vi")
LANG_NAMES = {"en": "English", "de": "German"}
CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]")


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
PROMPT_VERSION = 6


def cache_variant() -> str:
    """Identifies who produced an answer (model + prompt), used in the lookup cache key."""
    return f"{_config()[1]}|p{PROMPT_VERSION}"


def build_prompt(lang: str, word: str, sentence: str, lemma: str | None = None,
                 senses: list[str] | None = None) -> str:
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
    return (
        "You are a Vietnamese tutor for a B1 learner. Reply ONLY with JSON.\n"
        f"Task: explain ONE target {kind}, marked with [[ ]] in the sentence, exactly as it is used there. "
        f"Translate only the target {kind}, not the other words of the sentence.\n"
        + ("First read the numbered dictionary senses and pick the one that fits the sentence "
           "(often NOT the first one), then translate that sense.\n" if senses else "")
        + f"Keys: {sense_rules}"
        f'"translation_vi" (short Vietnamese equivalent of the target {kind}), '
        f'"gloss_vi" (Vietnamese explanation of what the target {kind} means in this sentence, max 25 words; '
        'use "—" for proper nouns or trivial words), '
        '"sense_vi" (short Vietnamese label of this sense, max 6 words).\n'
        "Write the Vietnamese values in Vietnamese only. No Chinese characters.\n\n"
        "Example (shows the format only, do not copy its content):\n"
        "Language: English\nTarget word: banks (base form: bank)\n"
        + ("Dictionary senses (English):\n1. (noun) An institution where one can place and borrow money.\n"
           "2. (noun) An edge of river, lake, or other watercourse.\n" if senses else "")
        + "Sentence: The children played on the [[banks]] of the river.\n"
        + ('{"sense_no": 2, ' if senses else "{")
        + '"translation_vi": "bờ sông", "gloss_vi": "Phần đất ở hai bên mép sông.", "sense_vi": "bờ (sông, hồ)"}\n\n'
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
    if not isinstance(data, dict) or not all(isinstance(data.get(k), str) for k in KEYS):
        raise ValueError("missing keys")
    out = {k: unicodedata.normalize("NFC", data[k].strip()) for k in KEYS}
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
    props = {k: {"type": "string"} for k in KEYS}
    if with_sense:
        props = {"sense_no": {"type": "integer"}, **props}
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


async def explain(lang: str, word: str, sentence: str, lemma: str | None = None,
                  senses: list[str] | None = None) -> dict:
    """Return {translation_vi, gloss_vi, sense_vi[, sense_no]}. Raises LLMError with a friendly message."""
    base, model, timeout = _config()
    prompt = build_prompt(lang, word, sentence, lemma, senses)
    # Separate connect timeout so "Ollama not running" is reported as such, not as a slow model.
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(10.0, timeout + 5))) as client:
        for attempt in (1, 2):  # retry once on unusable output
            raw = await _generate(client, base, model, prompt, answer_schema(bool(senses)))
            try:
                return parse_response(raw, len(senses or []))
            except (ValueError, json.JSONDecodeError) as e:
                log.warning("LLM attempt %d returned invalid JSON (%s): %r", attempt, e, raw[:300])
    raise LLMError("The local model returned an invalid answer. Try clicking the word again.")


# ---------- passage translation (selected sentences) ----------

PASSAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "translation_vi": {"type": "string"},
        "notes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"term": {"type": "string"}, "explanation_vi": {"type": "string"}},
                "required": ["term", "explanation_vi"],
            },
        },
    },
    "required": ["translation_vi", "notes"],
}


def build_passage_prompt(lang: str, text: str) -> str:
    return (
        "You are a Vietnamese tutor for a B1 learner. Reply ONLY with JSON.\n"
        f"Task: translate the {LANG_NAMES.get(lang, lang)} text into natural, fluent Vietnamese "
        "(keep the meaning and tone; do not translate word by word).\n"
        'Keys: "translation_vi" (the full Vietnamese translation), '
        '"notes" (up to 3 difficult words or expressions copied exactly from the text, each with '
        '"term" and "explanation_vi": a short Vietnamese explanation, max 15 words; [] if nothing is difficult).\n'
        "Write Vietnamese only. No Chinese characters.\n\n"
        "Example (shows the format only, do not copy its content):\n"
        "Text: She couldn't make up her mind, so she stayed at home.\n"
        '{"translation_vi": "Cô ấy không thể quyết định được, nên cô ở nhà.", '
        '"notes": [{"term": "make up her mind", "explanation_vi": "quyết định, đưa ra lựa chọn"}]}\n\n'
        f"Text: {text}\n"
        "Answer (JSON):\n"
    )


def parse_passage(raw: str, text: str) -> dict:
    data = json.loads(re.sub(r"<think>.*?</think>", "", raw, flags=re.S).strip().strip("`"))
    tr = unicodedata.normalize("NFC", str(data.get("translation_vi", "")).strip())
    if not tr:
        raise ValueError("empty translation")
    notes = []
    for n in data.get("notes") or []:
        if not isinstance(n, dict):
            continue
        term, expl = str(n.get("term", "")).strip(), str(n.get("explanation_vi", "")).strip()
        # Drop notes whose term isn't really in the text (small models invent some), and "notes"
        # that are whole sentences: they just repeat the translation.
        if term and expl and term.lower() in text.lower() and len(term.split()) <= 4:
            notes.append({"term": term, "explanation_vi": unicodedata.normalize("NFC", expl)})
    if CJK.search(tr) or any(CJK.search(n["explanation_vi"]) for n in notes):
        raise ValueError("contains Chinese characters")
    return {"translation_vi": tr, "notes": notes[:3]}


async def translate_passage(lang: str, text: str) -> dict:
    """Return {translation_vi, notes:[{term, explanation_vi}]}. Raises LLMError with a friendly message."""
    base, model, timeout = _config()
    prompt = build_passage_prompt(lang, text)
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(10.0, timeout + 5))) as client:
        for attempt in (1, 2):
            # A few sentences in Vietnamese plus notes need more tokens than a single word.
            raw = await _generate(client, base, model, prompt, PASSAGE_SCHEMA, num_predict=1000)
            try:
                return parse_passage(raw, text)
            except (ValueError, json.JSONDecodeError) as e:
                log.warning("Passage attempt %d invalid (%s): %r", attempt, e, raw[:300])
    raise LLMError("The local model returned an invalid answer. Try again.")
