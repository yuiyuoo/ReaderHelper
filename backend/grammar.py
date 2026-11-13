"""Grammar facts for the popover and the flashcards (German only for now), from the German Wiktionary:
nouns -> article (gender), plural, genitive; verbs -> er-form, Präteritum, Perfekt (with haben/sein);
adjectives -> comparative and superlative.

de.wiktionary pages contain fixed tables ("Deutsch Substantiv Übersicht", "Deutsch Verb Übersicht", …)
with one parameter per line, so reading the raw wikitext is simple and reliable."""
import logging
import re

import httpx
import simplemma

from .nlp import HTTP_HEADERS

log = logging.getLogger("readerhelper.grammar")

ARTICLES = {"m": "der", "f": "die", "n": "das", "pl": "die"}
SPACY_GENDER = {"Masc": "m", "Fem": "f", "Neut": "n"}
AUX_FORM = {"haben": "hat", "sein": "ist"}

# lemma -> parsed German entries, or None for "no page". Network errors are not cached.
_CACHE: dict[str, list[dict] | None] = {}
_CACHE_MAX = 3000


async def _entries(client: httpx.AsyncClient, title: str) -> list[dict] | None:
    """All tables in the German section of a de.wiktionary page: [{"type": "Substantiv", "irregular": …,
    "params": {…}}, …]. None if there is no page or no German section."""
    if title in _CACHE:
        return _CACHE[title]
    r = await client.get("https://de.wiktionary.org/w/index.php", params={"title": title, "action": "raw"})
    if r.status_code in (403, 429) or r.status_code >= 500:
        raise httpx.HTTPError(f"HTTP {r.status_code}")
    result = _parse(r.text) if r.status_code == 200 else None
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[title] = result
    return result


def _parse(text: str) -> list[dict] | None:
    m = re.search(r"^== .*\{\{Sprache\|Deutsch\}\}.*==\s*$", text, re.M)
    if not m:
        return None
    section = text[m.end():]
    if (nxt := re.search(r"^== ", section, re.M)):
        section = section[: nxt.start()]
    entries, heading = [], ""
    lines = section.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("=== "):
            heading = line
        elif (t := re.match(r"\{\{Deutsch (Substantiv|Verb|Adjektiv) Übersicht\s*$", line)):
            params = {}
            i += 1
            while i < len(lines) and not lines[i].startswith("}}"):
                if (p := re.match(r"\|([^=]+)=(.*)", lines[i])):
                    params[p.group(1).strip()] = _clean(p.group(2))
                i += 1
            entries.append({"type": t.group(1), "params": params,
                            "irregular": bool(re.search(r"unregelmäßig|\{\{unreg\.\}\}", heading))})
        i += 1
    return entries


def _clean(v: str) -> str:
    v = re.sub(r"<[^>]+>|\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", v)  # tags, [[link|text]] -> text
    v = v.strip()
    return "" if v in ("—", "-", "–", "") else v


def _first(params: dict, *keys: str) -> str:
    """Value of the first key that has one; tables number alternatives ("Nominativ Plural 1")."""
    for k in keys:
        for kk in (k, f"{k} 1"):
            if params.get(kk):
                return params[kk]
    return ""


def _noun(e: dict, lemma: str) -> dict:
    p = e["params"]
    g = _first(p, "Genus")
    plural = _first(p, "Nominativ Plural")
    if g == "0":  # plural only ("die Eltern")
        return {"kind": "noun", "gender": "pl", "article": "die", "plural": None, "plural_only": True,
                "text": f"die {plural or lemma} (nur Plural)"}
    gender = g if g in ARTICLES else None
    sing = _first(p, "Nominativ Singular") or lemma
    gen = _first(p, "Genitiv Singular")
    parts = [f"{ARTICLES[gender]} {sing}" if gender else sing,
             f"die {plural}" if plural else "kein Plural"]
    if gen and gender:
        parts.append(f"{'des' if gender != 'f' else 'der'} {gen}")
    return {"kind": "noun", "gender": gender, "article": ARTICLES.get(gender), "plural": plural or None,
            "genitive": gen or None, "text": ", ".join(parts)}


def _verb(e: dict) -> dict | None:
    p = e["params"]
    er, past, part = _first(p, "Präsens_er, sie, es"), _first(p, "Präteritum_ich"), _first(p, "Partizip II")
    if not (er or past or part):
        return None
    auxes = [a for a in (p.get("Hilfsverb"), p.get("Hilfsverb*")) if a in AUX_FORM]
    perfect = f"{'/'.join(AUX_FORM[a] for a in auxes) or 'hat'} {part}" if part else ""
    return {"kind": "verb", "present": er, "past": past, "perfect": perfect, "aux": auxes,
            "irregular": e["irregular"], "text": " · ".join(x for x in (er, past, perfect) if x)}


def _adjective(e: dict) -> dict | None:
    p = e["params"]
    comp, sup = _first(p, "Komparativ"), _first(p, "Superlativ")
    if not comp:
        return None  # not comparable ("tot")
    sup = f"am {sup}" if sup and not sup.startswith("am ") else sup
    return {"kind": "adj", "comparative": comp, "superlative": sup, "text": " · ".join(x for x in (comp, sup) if x)}


WANTED = {"NOUN": "Substantiv", "VERB": "Verb", "AUX": "Verb", "ADJ": "Adjektiv", "ADV": "Adjektiv"}


def _pick(entries: list[dict], kind: str, gender: str | None) -> dict | None:
    same = [e for e in entries if e["type"] == kind]
    if kind == "Substantiv" and gender:
        # Several nouns on one page (das Band / der Band / die Band): the gender spaCy saw in the sentence decides.
        same = [e for e in same if _first(e["params"], "Genus") == gender] or same
    return same[0] if same else None


def _compound_tail(lemma: str) -> tuple[str, str] | None:
    """German compound nouns take gender and plural from the last part: "Haustürschlüssel" ->
    ("Haustür", "Schlüssel"). Returns the longest known last part, or None."""
    for i in range(2, len(lemma) - 2):
        tail = lemma[i:].capitalize()
        if len(tail) >= 3 and simplemma.is_known(tail, lang="de"):
            return lemma[:i], tail
    return None


async def grammar_of(lang: str, lemma: str, pos: str, morph_gender: str = "") -> dict | None:
    """Grammar facts for the word, or None (other language/part of speech, nothing found, offline).
    Never raises."""
    kind = WANTED.get(pos)
    if lang != "de" or not kind or " " in lemma.strip():
        return None
    gender = SPACY_GENDER.get(morph_gender)
    try:
        async with httpx.AsyncClient(timeout=6, headers=HTTP_HEADERS, follow_redirects=True) as client:
            entries = await _entries(client, lemma) or []
            e = _pick(entries, kind, gender)
            if e is None and kind == "Substantiv" and (split := _compound_tail(lemma)):
                head, tail = split
                if (t := _pick(await _entries(client, tail) or [], kind, gender)):
                    # Put the first part back on: "Schlüssel" -> "Haustürschlüssel".
                    params = {k: head + v.lower() if v and k.startswith(("Nominativ", "Genitiv")) else v
                              for k, v in t["params"].items()}
                    return {**_noun({**t, "params": params}, lemma), "from": tail}
    except httpx.HTTPError as ex:
        log.warning("grammar lookup failed: %s", ex)
        e = None
    if e is not None:
        if kind == "Substantiv":
            return _noun(e, lemma)
        return _verb(e) if kind == "Verb" else _adjective(e)
    if kind == "Substantiv" and gender:
        # No dictionary table: at least the gender spaCy read from the sentence (article, endings).
        return {"kind": "noun", "gender": gender, "article": ARTICLES[gender], "plural": None,
                "text": f"{ARTICLES[gender]} {lemma}", "guess": True}
    return None
