"""Regression check for German base forms, especially separable verbs ("fing ... an" -> anfangen).

Usage:  python tools/check_lemmas.py      (no server needed; runs spaCy + simplemma directly)

Each case: sentence, clicked word, expected base form. The last part checks highlighting: which words
of a few sentences would be marked for a saved base form.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import nlp

# (sentence, clicked word, expected base form)
cases = [
    # separated, many different particles and verbs
    ("Chiaras Freundlichkeit milderte den Widerwillen.", "milderte", "mildern"),  # spaCy: "milderen"
    ("Julia trat einen Schritt zurück und musterte Laura.", "musterte", "mustern"),  # spaCy: ADJA "mustert"
    ("Sie trug die rote Jacke.", "rote", "rot"),
    ("Sie lächelte und wanderte weiter.", "lächelte", "lächeln"),
    ("Der Zug kam pünktlich in Berlin an.", "kam", "ankommen"),
    ("Wir kauften am Samstag im Supermarkt ein.", "kauften", "einkaufen"),
    ("Sie nahm ihren kleinen Bruder mit.", "nahm", "mitnehmen"),
    ("Er kam nach drei Jahren endlich zurück.", "kam", "zurückkommen"),
    ("Das Kind sah sehr müde aus.", "sah", "aussehen"),
    ("Sie hielt das Seil mit beiden Händen fest.", "hielt", "festhalten"),
    ("Der Hund lief schnell weg.", "lief", "weglaufen"),
    ("Ich stelle dir meinen Freund vor.", "stelle", "vorstellen"),
    ("Am Abend sahen sie lange fern.", "sahen", "fernsehen"),
    ("Er nahm an dem Wettbewerb teil.", "nahm", "teilnehmen"),
    ("Das Konzert fand im Park statt.", "fand", "stattfinden"),
    ("Dann fing das Mädchen an zu weinen.", "fing", "anfangen"),
    ("Dann fing das Mädchen an zu weinen.", "an", "anfangen"),
    # verb first: imperative / question
    ("Mach bitte die Tür zu!", "Mach", "zumachen"),
    ("Mach bitte die Tür zu!", "zu", "zumachen"),
    ("Stehst du morgen früh auf?", "Stehst", "aufstehen"),
    ("Kommst du heute mit?", "Kommst", "mitkommen"),
    ("Hör mir gut zu!", "Hör", "zuhören"),
    ("Ruf mich morgen an!", "Ruf", "anrufen"),
    ("Komm bitte schnell her!", "Komm", "herkommen"),
    ("Pass gut auf!", "Pass", "aufpassen"),
    ("Schau dir das Bild an!", "Schau", "anschauen"),
    ("Nimmst du den Schirm mit?", "Nimmst", "mitnehmen"),
    # not separated
    ("Weil er früh aufsteht, ist er müde.", "aufsteht", "aufstehen"),
    ("Er will morgen früh aufstehen.", "aufstehen", "aufstehen"),
    ("Der Zug ist pünktlich angekommen.", "angekommen", "ankommen"),
    # traps: preposition / no separable verb / noun at the start
    ("Das Buch lag auf dem Tisch.", "lag", "liegen"),
    ("Er sah aus dem Fenster.", "sah", "sehen"),
    ("Sie ging mit ihrem Bruder in die Stadt.", "ging", "gehen"),
    ("Er kam zu spät.", "kam", "kommen"),
    ("Die Katze fing die Maus.", "fing", "fangen"),
    ("Kinder spielen auf der Straße.", "Kinder", "Kind"),
    ("Mädchen tanzten im Garten.", "Mädchen", "Mädchen"),
    ("Anna kam gestern an.", "kam", "ankommen"),
    ("Bücher lagen auf dem Boden.", "Bücher", "Buch"),
    ("Ruf ist wichtig für ihn.", "Ruf", "Ruf"),
    ("Hunde laufen gern mit.", "laufen", "mitlaufen"),
]
ok = 0
for s, w, exp in cases:
    got = nlp.lemmatize("de", w, s)[0]
    hit = got.lower() == exp.lower()
    ok += hit
    print(f"{'OK ' if hit else 'XX '} {w:11} -> {got:14} (expected {exp:13}) | {s}")
print(f"\n{ok}/{len(cases)} correct")

paras = ["Die Katze fing die Maus.", "Dann fing das Mädchen an zu weinen.", "Mach die Tür zu!", "Er machte das Fenster auf."]
for saved in ({"fangen"}, {"anfangen"}, {"zumachen"}, {"machen"}):
    spans = nlp.find_lemmas("de", paras, saved)
    print(sorted(saved), [[p[a:b] for a, b, *_ in sp] for p, sp in zip(paras, spans)])

# ---- noun-verb expressions ("in Frage stellen") ----
from backend import expressions  # noqa: E402

expr_cases = [  # (lang, sentence, clicked word, expected expression or None)
    ("de", "Er stellte die Entscheidung gestern in Frage.", "stellte", "in Frage stellen"),
    ("de", "Er stellte die Entscheidung gestern in Frage.", "Frage", "in Frage stellen"),
    ("de", "Sie traf schließlich eine Entscheidung.", "Entscheidung", "eine Entscheidung treffen"),
    ("de", "Die Entscheidung wurde gestern getroffen.", "getroffen", "eine Entscheidung treffen"),
    ("de", "Das wurde in Frage gestellt.", "Frage", "in Frage stellen"),
    ("de", "Er stellt ihr das Auto zur Verfügung.", "Verfügung", "zur Verfügung stellen"),
    ("de", "Er hat das Problem in Angriff genommen.", "Angriff", "in Angriff nehmen"),
    ("de", "Er stellte das Glas auf den Tisch.", "stellte", None),
    ("de", "Die Katze fing die Maus.", "Maus", None),
]
ok = 0
for lang, s, w, exp in expr_cases:
    r = expressions.find_at(lang, s, w)
    got = r.get("display") if r and "check" not in r else None
    hit = got == exp
    ok += hit
    print(f"{'OK ' if hit else 'XX '} {w:12} -> {got!s:28} (expected {exp}) | {s}")
print(f"\n{ok}/{len(expr_cases)} expressions correct")

paras = ["Er stellte die Entscheidung gestern in Frage.", "Das wurde in Frage gestellt.", "Er stellte eine Frage."]
for saved in ({"in Frage stellen"}, {"eine Frage stellen"}):
    spans = nlp.find_lemmas("de", paras, saved)
    print(sorted(saved), [[p[a:b] for a, b, *_ in sp] for p, sp in zip(paras, spans)])

# Own combinations ("+ Add word") and phrases: all words' base forms close together in one sentence,
# reflexive pronouns count as "sich" and are marked too (#59).
combo_cases = [
    ("darauf bestehen", "Sie bestand darauf, dass alle kommen.", ["bestand", "darauf"]),
    ("sich reissen", "Laura riss sich die Kopfhörer vom Kopf.", ["riss", "sich"]),
    ("sich wälzen", "Ich wälzte mich hin und her.", ["wälzte", "mich"]),
    ("zog die Vorhänge zu", "Sie zog schnell die schweren Vorhänge zu.", ["zog", "Vorhänge", "zu"]),
    ("halbherzig erwiderte", "Sie erwiderte halbherzig.", ["erwiderte", "halbherzig"]),
    ("darauf bestehen", "Er bestand die Prüfung und freute sich darauf.", []),
    ("sich wälzen", "Er wäscht sich die Hände.", []),
    # line break + indentation from the EPUB source inside the sentence (#60)
    ("aufwirbeln", "Jeder Schritt\n            wirbelte kleine Staubwolken auf, die klebten.", ["wirbelte", "auf"]),
    ("in Frage stellen", "Er stellte  alles\n   in Frage.", ["stellte", "in", "Frage"]),
]
ok = 0
for saved, s, exp in combo_cases:
    got = [s[a:b] for a, b, *_ in nlp.find_lemmas("de", [s], {saved})[0]]
    hit = got == exp
    ok += hit
    print(f"{'OK ' if hit else 'XX '} {saved:22} -> {got!s:32} | {s}")
print(f"\n{ok}/{len(combo_cases)} combinations correct")
