"""Language detection, lemmatization, sentence splitting and dictionary lookup."""
import html
import logging
import re
from functools import lru_cache

import httpx
import spacy
from langdetect import DetectorFactory, LangDetectException, detect_langs

DetectorFactory.seed = 0  # make langdetect deterministic
log = logging.getLogger(__name__)

MODELS = {"en": "en_core_web_sm", "de": "de_core_news_sm"}
# Wikimedia rejects generic User-Agents (403 "robot policy"); this form is accepted.
HTTP_HEADERS = {
    "User-Agent": "ReaderHelper/0.1 (local language-learning EPUB reader; "
    "https://www.mediawiki.org/wiki/API:Etiquette) httpx"
}


@lru_cache(maxsize=2)
def get_nlp(lang: str):
    return spacy.load(MODELS[lang])


def detect_lang(sentence: str, hint: str | None = None) -> str:
    """Collapse langdetect output to 'en' or 'de'. `hint` (book language) breaks ties on short text."""
    hint = (hint or "").lower()[:2]
    hint = hint if hint in MODELS else None
    try:
        probs = {l.lang: l.prob for l in detect_langs(sentence)}
    except LangDetectException:
        return hint or "en"
    en, de = probs.get("en", 0.0), probs.get("de", 0.0)
    if hint and max(en, de) < 0.8:
        return hint
    if en == de == 0.0:
        return hint or "en"
    return "de" if de > en else "en"


def _norm(s: str) -> str:
    return s.strip(" \t\n'’\"“”„«»‚‘.,;:!?()[]").lower()


def pick_sentence(lang: str, word: str, sentence: str) -> str:
    """If the client sent more than one sentence, keep only the one containing the word."""
    doc = get_nlp(lang)(sentence)
    sents = [s.text.strip() for s in doc.sents if s.text.strip()]
    if len(sents) <= 1:
        return sentence.strip()
    w = _norm(word)
    for s in sents:
        if any(_norm(t) == w for t in re.split(r"\s+", s)):
            return s
    return sentence.strip()


def lemmatize(lang: str, word: str, sentence: str) -> tuple[str, str]:
    """Return (lemma, coarse POS tag). German separable verbs are joined: "stand ... auf" -> "aufstehen"."""
    nlp = get_nlp(lang)
    if " " in word.strip():
        # Multi-word selection (phrase/idiom): English "gave up" -> "give up"; German phrases are kept
        # as written, since token lemmas would mangle them ("zu Hause" -> "zu Haus").
        if lang == "en":
            return " ".join(t.lemma_.lower() for t in nlp(word) if not t.is_punct), ""
        return word.strip(), ""
    w = _norm(word)
    tok = next((t for t in nlp(sentence) if t.text.lower() == w), None)
    if tok is None:
        doc = nlp(word)
        tok = doc[0] if len(doc) else None
    if tok is None:
        return word, ""
    lemma, pos = tok.lemma_.strip() or word, tok.pos_
    if lang == "de":
        # spaCy marks a separated verb particle with dep "svp", attached to its verb.
        if tok.dep_ == "svp" and tok.head.pos_ in ("VERB", "AUX"):
            lemma, pos = tok.text.lower() + tok.head.lemma_, "VERB"
        elif tok.pos_ in ("VERB", "AUX"):
            particle = next((c for c in tok.children if c.dep_ == "svp"), None)
            if particle is not None:
                lemma = particle.text.lower() + lemma
    # German nouns are capitalised; English lemmas lowercase (except proper nouns like "I"/names).
    if lang == "en" and not (lemma[:1].isupper() and word[:1].isupper()):
        lemma = lemma.lower()
    return lemma, pos


def _strip_html(s: str) -> str:
    s = re.sub(r"<(style|script)\b.*?</\1>", "", s, flags=re.S | re.I)
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    return re.sub(r"\s+", " ", s).strip()


def _short(s: str, limit: int = 180) -> str:
    return s if len(s) <= limit else s[: limit - 1].rsplit(" ", 1)[0] + "…"


class DictUnavailable(Exception):
    pass


def _check(r: httpx.Response) -> bool:
    if r.status_code in (403, 429) or r.status_code >= 500:
        raise DictUnavailable(f"HTTP {r.status_code}")
    return r.status_code == 200


# Senses come back as groups (one per dictionary entry / part of speech) so we can sample each group.
Groups = list[tuple[str, list[str]]]


async def _en_senses(client: httpx.AsyncClient, term: str) -> Groups:
    r = await client.get(f"https://api.dictionaryapi.dev/api/v2/entries/en/{term}")
    if not _check(r):
        return []
    return [
        (m.get("partOfSpeech", ""), [d["definition"] for d in m.get("definitions", []) if d.get("definition")])
        for entry in r.json()
        for m in entry.get("meanings", [])
    ]


async def _de_senses(client: httpx.AsyncClient, term: str) -> Groups:
    # en.wiktionary's REST API has German entries (definitions are written in English).
    # Wiktionary page titles use underscores for spaces ("zu_Hause").
    r = await client.get(f"https://en.wiktionary.org/api/rest_v1/page/definition/{term.replace(' ', '_')}")
    if not _check(r):
        return []
    return [
        (sec.get("partOfSpeech", ""),
         [t for t in (_strip_html(d.get("definition", "")) for d in sec.get("definitions", [])) if t])
        for sec in r.json().get("de", [])
    ]


# spaCy coarse POS -> part-of-speech names used by the dictionaries (lowercased).
POS_NAMES = {
    "NOUN": {"noun"}, "PROPN": {"proper noun", "noun"}, "VERB": {"verb"}, "AUX": {"verb", "auxiliary verb"},
    "ADJ": {"adjective", "participle"}, "ADV": {"adverb"}, "ADP": {"preposition", "postposition"},
    "PRON": {"pronoun"}, "DET": {"determiner", "article", "pronoun"}, "NUM": {"numeral", "number"},
    "CCONJ": {"conjunction"}, "SCONJ": {"conjunction"}, "PART": {"particle", "adverb"},
    "INTJ": {"interjection"},
}
MAX_SENSES = 5


def _select(groups: Groups, pos: str) -> list[str]:
    """Pick up to MAX_SENSES: only groups matching spaCy's POS (if any match), and the first
    senses of every group before later ones, so e.g. the river sense of "bank" isn't crowded out."""
    groups = [(p, defs) for p, defs in groups if defs]
    wanted = POS_NAMES.get(pos)
    if wanted:
        groups = [g for g in groups if g[0].lower() in wanted] or groups
    out, depth = [], 0
    while len(out) < MAX_SENSES and any(depth < len(d) for _, d in groups):
        for p, defs in groups:
            if depth < len(defs) and len(out) < MAX_SENSES:
                out.append(_short(f"({p}) {defs[depth]}" if p else defs[depth]))
        depth += 1
    return out


async def fetch_senses(lang: str, lemma: str, word: str, pos: str = "") -> tuple[list[str], str | None]:
    """Return (senses, error_message), senses formatted "(pos) definition". Never raises."""
    candidates = []
    for t in (lemma, word, word.lower(), word.capitalize()):
        if t and t not in candidates:
            candidates.append(t)
    fetch = _en_senses if lang == "en" else _de_senses
    try:
        async with httpx.AsyncClient(timeout=8, headers=HTTP_HEADERS, follow_redirects=True) as client:
            for term in candidates:
                senses = _select(await fetch(client, term), pos)
                if senses:
                    return senses, None
        return [], "No dictionary entry found for this word."
    except (httpx.HTTPError, ValueError, DictUnavailable) as e:
        log.warning("dictionary lookup failed: %s", e)
        return [], "Dictionary service is unreachable right now."
