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
    print(sorted(saved), [[p[a:b] for a, b in sp] for p, sp in zip(paras, spans)])
