"""Language detection, lemmatization, sentence splitting and dictionary lookup."""
import asyncio
import html
import logging
import re
from functools import lru_cache

import httpx
import simplemma
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


def _base_form(lang: str, tok) -> str:
    """spaCy's lemma, or (German only) simplemma's when spaCy left the word unchanged. The small German
    spaCy model doesn't know many irregular forms ("fing", "molk", "aß"), which simplemma's word lists do.
    Not used for English: spaCy is good there, and simplemma turns adjectives like "tired" into "tire"."""
    lemma = tok.lemma_.strip() or tok.text
    if lang != "de" or tok.pos_ in ("PROPN", "PUNCT", "NUM"):
        return lemma
    # Ask simplemma when spaCy left the word unchanged, or produced something that isn't a German word
    # (spaCy guessed "fortwarfsen" for "fortwarfst"; simplemma knows "fortwerfen").
    if lemma.lower() == tok.text.lower() or not simplemma.is_known(lemma, lang="de"):
        alt = simplemma.lemmatize(tok.text, lang="de")
        if alt and alt.lower() != tok.text.lower() and simplemma.is_known(alt, lang="de"):
            return alt
    return lemma


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
    return token_lemma(lang, tok)


# German separable verbs ("fing ... an" = anfangen). spaCy's parser often misses the link when words come
# between verb and particle ("Dann fing das Mädchen an zu weinen" -> dep "op", not "svp"), but its tagger
# still tags the particle PTKVZ ("abgetrennter Verbzusatz"). So: a PTKVZ particle belongs to the nearest
# finite verb before it in the sentence, if verb + particle is a known German word.
_FINITE = {"VVFIN", "VAFIN", "VMFIN"}


def _verb_bases(tok) -> list[str]:
    """Possible infinitives of `tok` if it is (or, at the start of a sentence, may be) a verb.
    spaCy's small model tags a capitalised verb at the start of a sentence as a name or noun
    ("Mach die Tür zu!", "Stehst du früh auf?"), so there we try simplemma on the lowercased word and,
    for imperatives like "Ruf"/"Komm", the stem + "en". Only lowercase forms ending in -n count as verbs
    (simplemma reads "Ruf" as the noun "der Ruf")."""
    is_verb = tok.pos_ in ("VERB", "AUX")
    first_word = tok.i == tok.sent.start and tok.text[:1].isupper()
    if not (is_verb or first_word):
        return []
    low = tok.text.lower()
    tries = [_base_form("de", tok)] if is_verb else []
    if first_word:
        tries += [simplemma.lemmatize(low, lang="de"), low + "en", low + "n"]
    # Even a token tagged VERB can get a noun as lemma ("Ruf" -> "Ruf"), so the shape check applies to all.
    return [t for t in dict.fromkeys(tries)
            if t and t.islower() and t.endswith("n") and (t != low or is_verb)]


def _verb_base(tok) -> str | None:
    bases = _verb_bases(tok)
    return bases[0] if bases else None


def _compound(particle, verb) -> str | None:
    """particle + verb's infinitive, if that is a known German word ("an" + "fangen" = "anfangen")."""
    for base in _verb_bases(verb):
        word = particle.text.lower() + base
        if simplemma.is_known(word, lang="de"):
            return word
    return None


def _verb_of_particle(tok):
    """The verb a separated particle belongs to, or None."""
    if tok.dep_ != "svp" and tok.tag_ != "PTKVZ":
        return None
    sent = tok.sent
    candidates = []
    if tok.dep_ == "svp":
        candidates.append(tok.head)  # the parser linked them itself
    candidates.append(next((t for t in reversed(sent[: tok.i - sent.start]) if t.tag_ in _FINITE), None))
    candidates.append(sent[0])       # imperative / question: the verb comes first
    for verb in candidates:
        if verb is not None and verb.i < tok.i and _compound(tok, verb):
            return verb
    return None


def _particle_of_verb(verb):
    """The separated particle of a verb ("an" for "fing" in "fing … an"), or None."""
    sent = verb.sent
    for t in sent[verb.i - sent.start + 1:]:
        if t.dep_ == "svp" or t.tag_ == "PTKVZ":
            v = _verb_of_particle(t)
            # Compare positions: spaCy creates a new Token object on every access, so `is` never matches.
            if v is not None and v.i == verb.i:
                return t
    return None


def token_lemma(lang: str, tok) -> tuple[str, str]:
    """(lemma, coarse POS) of one spaCy token in its sentence. Used for clicked words and for highlighting
    saved words, so both always agree. German separable verbs are joined: "fing ... an" -> "anfangen"."""
    lemma, pos = _base_form(lang, tok), tok.pos_
    if lang == "de":
        if (verb := _verb_of_particle(tok)) is not None:
            lemma, pos = _compound(tok, verb), "VERB"
        elif _verb_base(tok) is not None and (particle := _particle_of_verb(tok)) is not None:
            lemma, pos = _compound(particle, tok), "VERB"
    # German nouns are capitalised; English lemmas lowercase (except proper nouns like "I"/names).
    if lang == "en" and not (lemma[:1].isupper() and tok.text[:1].isupper()):
        lemma = lemma.lower()
    return lemma, pos


# Per-paragraph analysis cache: (lang, text) -> [(start, end, lemma lowercased), ...]. Parsing a chapter
# takes a few seconds; with this cache, re-highlighting after saving a word is instant.
_PARA_CACHE: dict[tuple[str, str], list[tuple[int, int, str]]] = {}
_PARA_CACHE_MAX = 20000


def find_lemmas(lang: str, paragraphs: list[str], lemmas: set[str]) -> list[list[list[int]]]:
    """For each paragraph, [start, end] character spans of the words whose lemma is in `lemmas`
    (compared case-insensitively). For a separable verb both parts count ("fing" and "an" of anfangen)."""
    wanted = {l.lower() for l in lemmas}
    todo = [p for p in dict.fromkeys(paragraphs) if (lang, p) not in _PARA_CACHE]
    if todo:
        for text, doc in zip(todo, get_nlp(lang).pipe(todo, batch_size=64)):
            if len(_PARA_CACHE) >= _PARA_CACHE_MAX:
                _PARA_CACHE.clear()  # simple bound; it refills from what is being read
            _PARA_CACHE[(lang, text)] = [
                (t.idx, t.idx + len(t.text), token_lemma(lang, t)[0].lower())
                for t in doc if t.is_alpha
            ]
    return [[[s, e] for s, e, lem in _PARA_CACHE[(lang, p)] if lem in wanted] for p in paragraphs]


def _strip_html(s: str) -> str:
    s = re.sub(r"<(style|script)\b.*?</\1>", "", s, flags=re.S | re.I)
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    return re.sub(r"\s+", " ", s).strip()


def _short(s: str, limit: int = 180) -> str:
    return s if len(s) <= limit else s[: limit - 1].rsplit(" ", 1)[0] + "…"


async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    """GET, with one retry after a short pause when the service says "too many requests" (429).
    Wiktionary does that after a quick series of lookups."""
    r = await client.get(url)
    if r.status_code == 429:
        await asyncio.sleep(1.5)
        r = await client.get(url)
    return r


class DictUnavailable(Exception):
    pass


def _check(r: httpx.Response) -> bool:
    if r.status_code in (403, 429) or r.status_code >= 500:
        raise DictUnavailable(f"HTTP {r.status_code}")
    return r.status_code == 200


# Senses come back as groups (one per dictionary entry / part of speech) so we can sample each group.
Groups = list[tuple[str, list[str]]]


async def _en_senses(client: httpx.AsyncClient, term: str) -> Groups:
    r = await _get(client, f"https://api.dictionaryapi.dev/api/v2/entries/en/{term}")
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
    r = await _get(client, f"https://en.wiktionary.org/api/rest_v1/page/definition/{term.replace(' ', '_')}")
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


# Small in-memory cache: a word lookup asks for the senses twice (quick step, then with the model).
# Network errors are not cached, so a short outage doesn't stick.
_SENSE_CACHE: dict[tuple, tuple[list[str], str | None]] = {}
_SENSE_CACHE_MAX = 1000


async def fetch_senses(lang: str, lemma: str, word: str, pos: str = "") -> tuple[list[str], str | None]:
    """Return (senses, error_message), senses formatted "(pos) definition". Never raises."""
    key = (lang, lemma, word, pos)
    if key in _SENSE_CACHE:
        return _SENSE_CACHE[key]
    result = await _fetch_senses(lang, lemma, word, pos)
    if result[0] or result[1] == NOT_FOUND:
        if len(_SENSE_CACHE) >= _SENSE_CACHE_MAX:
            _SENSE_CACHE.pop(next(iter(_SENSE_CACHE)))  # drop the oldest entry
        _SENSE_CACHE[key] = result
    return result


NOT_FOUND = "No dictionary entry found for this word."


async def _fetch_senses(lang: str, lemma: str, word: str, pos: str) -> tuple[list[str], str | None]:
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
        return [], NOT_FOUND
    except (httpx.HTTPError, ValueError, DictUnavailable) as e:
        log.warning("dictionary lookup failed: %s", e)
        return [], "Dictionary service is unreachable right now."
