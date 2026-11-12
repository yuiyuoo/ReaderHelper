"""Noun-verb combinations ("in Frage stellen", "eine Entscheidung treffen", "take into account").

The meaning of such an expression lies in the combination, and its parts are often far apart:
"Er stellte die Entscheidung gestern in Frage". spaCy's parse finds candidates: a verb with its object
noun, or with a prepositional phrase (preposition + noun). A candidate counts as an expression if it is in
the list `data/expressions_de.txt`; when a word is clicked, candidates not in the list can still be
confirmed by a dictionary entry (checked by the caller, see `main.lookup`).

Every expression has a *key* for matching: the words lowercased, without articles, verb in the infinitive
(so "Er traf die Entscheidung" and "eine Entscheidung treffen" share the key "entscheidung treffen").
"""
from functools import lru_cache
from pathlib import Path

from . import nlp

ARTICLES = {
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "einer", "eines",
    "kein", "keine", "keinen", "keinem", "keiner", "sich", "a", "an", "the",
}
VERB_POS = ("VERB", "AUX")
# Which children of a verb can be the noun of an expression, per language (spaCy dependency labels).
NOUN_DEPS = {"de": {"oa", "da", "og", "pd"}, "en": {"dobj", "obj", "attr", "nsubjpass"}}
PREP_DEPS = {"de": {"mo", "op", "cvc", "mnr"}, "en": {"prep"}}
PREP_NOUN_DEPS = {"de": {"nk"}, "en": {"pobj"}}
DATA = Path(__file__).resolve().parent / "data"


def key_of(words) -> str:
    """Matching key: lowercase words without articles."""
    return " ".join(w.lower() for w in words if w.lower() not in ARTICLES)


@lru_cache(maxsize=1)
def known_de() -> dict[str, str]:
    """{key: expression as written in the list}"""
    out = {}
    for line in (DATA / "expressions_de.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out[key_of(line.split())] = line
    return out


def _is_noun(tok) -> bool:
    return tok.pos_ in ("NOUN", "PROPN")


def _infinitive(lang: str, verb) -> str:
    return nlp.token_lemma(lang, verb)[0]


def _display(lang: str, verb, parts) -> str:
    """Dictionary-style form of verb + parts: German "in Frage stellen", English "take into account"."""
    words = [t.text.lower() if t.pos_ == "ADP" else nlp.token_lemma(lang, t)[0]
             for t in sorted(parts, key=lambda t: t.i)]
    inf = _infinitive(lang, verb)
    if lang == "en":
        particles = [c.text.lower() for c in verb.children if c.dep_ == "prt"]
        return " ".join([inf, *particles, *words])
    return " ".join([*words, inf])


def _verb_candidates(lang: str, verb) -> list[list]:
    """Noun / preposition+noun groups that can form an expression with `verb` (list of token lists)."""
    groups, holders = [], [verb]
    if lang == "de" and verb.dep_ == "oc" and verb.head.pos_ == "AUX":
        holders.append(verb.head)  # passive / perfect: "Die Entscheidung wurde getroffen"
    for h in holders:
        for c in h.children:
            if _is_noun(c) and (c.dep_ in NOUN_DEPS[lang] or (h is not verb and c.dep_ == "sb")):
                groups.append([c])
            elif c.pos_ == "ADP" and c.dep_ in PREP_DEPS[lang]:
                noun = next((n for n in c.children if _is_noun(n) and n.dep_ in PREP_NOUN_DEPS[lang]), None)
                if noun is not None:
                    groups.append([c, noun])
    return groups


def _verb_of(tok):
    """The verb an expression around `tok` would belong to, or None."""
    if tok.pos_ in VERB_POS:
        # an auxiliary with a participle/infinitive ("wurde ... gestellt") stands for that verb
        oc = next((c for c in tok.children if c.dep_ == "oc" and c.pos_ in VERB_POS), None)
        return oc if tok.pos_ == "AUX" and oc is not None else tok
    if tok.pos_ == "ADP":
        return tok.head if tok.head.pos_ in VERB_POS else None
    if _is_noun(tok):
        h = tok.head
        if h.pos_ == "ADP":
            h = h.head
        if h.pos_ == "AUX":  # passive subject: "Die Entscheidung wurde getroffen"
            return next((c for c in h.children if c.dep_ == "oc" and c.pos_ in VERB_POS), None)
        return h if h.pos_ in VERB_POS else None
    return None


def _same(a, b) -> bool:
    return a.i == b.i  # spaCy makes a new Token object on every access, so compare positions


def _as_result(lang: str, verb, group, sentence: str, display: str | None, source: str) -> dict:
    tokens = sorted([verb, *group] + _particles(lang, verb), key=lambda t: t.i)
    return {
        "display": display or _display(lang, verb, group),
        "key": key_of(_display(lang, verb, group).split()),
        "spans": [[t.idx, t.idx + len(t.text)] for t in tokens],
        "words": _written(tokens, sentence),
        "source": source,
    }


def _particles(lang: str, verb) -> list:
    """Separated parts of the verb itself: German "an" in "fing ... an", English "up" in "gave ... up"."""
    if lang == "de":
        p = nlp._particle_of_verb(verb)
        return [p] if p is not None else []
    return [c for c in verb.children if c.dep_ == "prt"]


def _written(tokens, sentence: str) -> str:
    """The parts as they appear in the sentence, "…" where words are left out: "stellte … in Frage"."""
    out, prev = [], None
    for t in tokens:
        if prev is not None and t.i != prev.i + 1:
            out.append("…")
        out.append(t.text)
        prev = t
    return " ".join(out)


def find_at(lang: str, sentence: str, word: str, extra: list[str] | None = None) -> dict | None:
    """Expression around the clicked `word` in `sentence`.

    Returns {"display", "key", "spans", "words", "source"} when found (source "list" or "manual"),
    {"check": [candidate, …]} when there are candidates that only a dictionary can confirm, or None.
    `extra`: further words the user picked with "+ Add word" (an expression built by hand)."""
    doc = nlp.get_nlp(lang)(sentence)
    w = word.strip().lower()
    tok = next((t for t in doc if t.text.lower() == w), None)
    if tok is None:
        return None
    if extra:
        return _manual(lang, doc, tok, extra, sentence)
    verb = _verb_of(tok)
    if verb is None:
        return None
    groups = [g for g in _verb_candidates(lang, verb)
              if _same(tok, verb) or any(_same(tok, t) for t in g) or tok.dep_ in ("svp", "prt")]
    if not groups:
        return None
    if lang == "de":
        listed = known_de()
        for g in groups:
            k = key_of(_display(lang, verb, g).split())
            if k in listed:
                return _as_result(lang, verb, g, sentence, listed[k], "list")
    checks = [_with_titles(lang, verb, g, _as_result(lang, verb, g, sentence, None, "dictionary"))
              for g in groups if _worth_checking(lang, g)][:2]
    return {"check": checks} if checks else None


def _worth_checking(lang: str, group) -> bool:
    """Fixed expressions usually have no article on the noun ("in Frage", "Rücksicht nehmen"), while
    ordinary use has one ("auf den Tisch", "die Maus"). Only article-less candidates are looked up in the
    dictionary, so a normal click doesn't cost extra requests. English also allows "a/an"
    ("make a decision")."""
    noun = group[-1]
    dets = [c.text.lower() for c in noun.children if c.pos_ == "DET"]
    if lang == "en":
        return all(d in ("a", "an") for d in dets)
    return not dets


def _with_titles(lang: str, verb, group, result: dict) -> dict:
    """Dictionary page titles to try for a candidate: "take into account"; English also "make a decision"."""
    titles = [result["display"]]
    noun = group[-1]
    det = next((c.text.lower() for c in noun.children if c.pos_ == "DET"), None)
    if lang == "en" and det in ("a", "an"):
        titles.append(result["display"].replace(nlp.token_lemma(lang, noun)[0], f"{det} {nlp.token_lemma(lang, noun)[0]}", 1))
    return {**result, "titles": titles}


def _manual(lang: str, doc, tok, extra: list[str], sentence: str) -> dict | None:
    """Expression from the clicked word + the words added with "+ Add word"."""
    chosen = [tok]
    for e in extra:
        e = e.strip().lower()
        t = next((t for t in doc if t.text.lower() == e and not any(_same(t, c) for c in chosen)), None)
        if t is not None:
            chosen.append(t)
    if len(chosen) < 2:
        return None
    # A chosen noun inside a prepositional phrase brings its preposition ("Frage" -> "in Frage").
    for t in list(chosen):
        if _is_noun(t) and t.head.pos_ == "ADP" and not any(_same(t.head, c) for c in chosen):
            chosen.append(t.head)
    verbs = [t for t in chosen if t.pos_ in VERB_POS]
    tokens = sorted(chosen, key=lambda t: t.i)
    if verbs:
        verb = verbs[0]
        rest = [t for t in tokens if not _same(t, verb)]
        display = _display(lang, verb, rest)
        tokens = sorted(tokens + [p for p in _particles(lang, verb) if not any(_same(p, c) for c in tokens)],
                        key=lambda t: t.i)
    else:
        display = " ".join(t.text for t in tokens)
    return {
        "display": display,
        "key": key_of(display.split()),
        "spans": [[t.idx, t.idx + len(t.text)] for t in tokens],
        "words": _written(tokens, sentence),
        "source": "manual",
    }


def all_in(lang: str, doc) -> list[tuple[str, list[list[int]]]]:
    """Every candidate expression in a parsed paragraph: [(key, spans), …]. Used for highlighting saved
    expressions (no list or dictionary check needed: the user saved them)."""
    out = []
    for verb in doc:
        if verb.pos_ not in VERB_POS:
            continue
        for g in _verb_candidates(lang, verb):
            tokens = sorted([verb, *g] + _particles(lang, verb), key=lambda t: t.i)
            out.append((key_of(_display(lang, verb, g).split()), [[t.idx, t.idx + len(t.text)] for t in tokens]))
    return out


def mark(sentence: str, spans: list[list[int]]) -> str:
    """Sentence with every part of the expression wrapped in [[ ]] for the model."""
    out, pos = [], 0
    for s, e in sorted(spans):
        out += [sentence[pos:s], "[[", sentence[s:e], "]]"]
        pos = e
    return "".join(out) + sentence[pos:]
