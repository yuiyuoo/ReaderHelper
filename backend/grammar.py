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
    """Parsed German section of a de.wiktionary page (see _parse), cached. None if there is no page."""
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
    """One entry per part-of-speech heading of the German section ("=== {{Wortart|Substantiv|Deutsch}} ==="):
    {"type": table or heading type, "kinds": all Wortart names, "params": the grammar table (may be empty),
    "senses": German meanings, "irregular": …}. None if there is no German section."""
    m = re.search(r"^== .*\{\{Sprache\|Deutsch\}\}.*==\s*$", text, re.M)
    if not m:
        return None
    section = text[m.end():]
    if (nxt := re.search(r"^== ", section, re.M)):
        section = section[: nxt.start()]
    entries, cur, in_meanings, group = [], None, False, ""
    lines = section.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("=== "):
            kinds = re.findall(r"\{\{Wortart\|([^|}]+)\|Deutsch\}\}", line)
            cur = {"type": kinds[0] if kinds else "", "kinds": kinds, "params": {}, "senses": [],
                   "irregular": bool(re.search(r"unregelmäßig|\{\{unreg\.\}\}", line))}
            entries.append(cur)
            in_meanings, group = False, ""
        elif cur is None:
            pass
        elif (t := re.match(r"\{\{Deutsch (Substantiv|Verb|Adjektiv) Übersicht\s*$", line)):
            i += 1
            while i < len(lines) and not lines[i].startswith("}}"):
                if (p := re.match(r"\|([^=]+)=(.*)", lines[i])):
                    cur["params"][p.group(1).strip()] = _clean(p.group(2))
                i += 1
            cur["type"] = t.group(1)
        elif line.startswith("{{Bedeutungen}}"):
            in_meanings, group = True, ""
        elif in_meanings:
            if line.startswith("{{"):          # the next block ({{Herkunft}}, {{Synonyme}}, …)
                in_meanings = False
            elif line.startswith("*"):         # group label for the following senses ("*{{K|intrans.}}")
                group = _clean_sense(line.lstrip("*"))
            elif (sm := re.match(r":\[[^\]]*\]\s*(.*)", line)):   # ":[1] …"; "::[1a] …" sub-senses are skipped
                if (sense := _clean_sense(sm.group(1))):
                    cur["senses"].append(f"{group} {sense}" if group and not sense.startswith(group) else sense)
        i += 1
    return entries


def _clean_sense(v: str) -> str:
    """Wiki markup -> plain text: "{{K|intrans.|zeitlich}} [[beginnen]]" -> "intrans., zeitlich: beginnen"."""
    v = re.sub(r"<!--.*?-->|<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", v, flags=re.S)

    def template(m: re.Match) -> str:
        parts = [x.strip() for x in m.group(1).split("|")]
        name, args = parts[0], [x for x in parts[1:] if "=" not in x]
        if name == "K":                       # context labels
            return ", ".join(args) + ":" if args else ""
        if name.startswith(("Wikipedia", "Ref-", "Lit-")):
            return ""
        if not args or args == [":"]:         # label templates: {{ugs.}}, {{übertr.|:}}
            return name + (":" if args == [":"] else "")
        return args[-1]
    for _ in range(3):                        # nested templates, innermost first
        v = re.sub(r"\{\{([^{}]*)\}\}", template, v)
    v = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", v)   # [[link|text]] -> text
    v = re.sub(r"'{2,}", "", v).replace("&nbsp;", " ")
    v = re.sub(r"<[^>]+>", "", v)
    return re.sub(r"\s+", " ", v).strip(" ;,")


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
    same = [e for e in entries if e["type"] == kind and e["params"]]  # entries with a grammar table
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


# spaCy part of speech -> de.wiktionary "Wortart" names, to show the meanings of the right word class.
SENSE_KINDS = {
    "NOUN": {"Substantiv"}, "PROPN": {"Eigenname", "Vorname", "Nachname", "Toponym", "Substantiv"},
    "VERB": {"Verb"}, "AUX": {"Verb", "Hilfsverb", "Modalverb"},
    "ADJ": {"Adjektiv", "Partizip I", "Partizip II"}, "ADV": {"Adverb", "Adjektiv", "Pronominaladverb"},
    "ADP": {"Präposition", "Postposition"}, "CCONJ": {"Konjunktion"}, "SCONJ": {"Subjunktion", "Konjunktion"},
    "PRON": {"Pronomen", "Personalpronomen", "Possessivpronomen", "Demonstrativpronomen", "Relativpronomen",
             "Indefinitpronomen", "Reflexivpronomen", "Interrogativpronomen"},
    "DET": {"Artikel", "Possessivpronomen", "Demonstrativpronomen", "Indefinitpronomen", "Pronomen"},
    "PART": {"Partikel", "Gradpartikel", "Fokuspartikel", "Modalpartikel", "Negationspartikel", "Antwortpartikel"},
    "INTJ": {"Interjektion"}, "NUM": {"Numerale", "Kardinalzahl"},
}
NAME_KINDS = {"Nachname", "Vorname", "Toponym", "Eigenname"}


async def german_senses(lemma: str, pos: str, morph_gender: str = "") -> list[str]:
    """German meanings of a German word from de.wiktionary ("(Substantiv) Gegenstand zum …"), at most 5,
    for the word class spaCy found. Uses the page already fetched for the grammar line. [] if none/offline."""
    if " " in lemma.strip():
        return []
    gender = SPACY_GENDER.get(morph_gender)
    try:
        async with httpx.AsyncClient(timeout=6, headers=HTTP_HEADERS, follow_redirects=True) as client:
            entries = await _entries(client, lemma) or []
            if not any(e["senses"] for e in entries) and lemma[:1].islower() != lemma[:1].isupper():
                alt = lemma.capitalize() if lemma[:1].islower() else lemma.lower()
                entries = await _entries(client, alt) or []
    except httpx.HTTPError as ex:
        log.warning("German meanings lookup failed: %s", ex)
        return []
    entries = [e for e in entries if e["senses"]]
    wanted = SENSE_KINDS.get(pos, set())
    same = [e for e in entries if wanted & set(e["kinds"])]
    if pos == "NOUN":  # surnames are also tagged "Substantiv"; for an ordinary noun they're noise
        same = [e for e in same if not NAME_KINDS & set(e["kinds"])] or same
    same = same or entries
    if gender:  # das Band / der Band / die Band
        same = [e for e in same if _first(e["params"], "Genus") in ("", gender)] or same
    from .nlp import _select  # breadth-first over the entries, "(pos) definition", shortened
    return _select([(e["type"], e["senses"]) for e in same], "")
