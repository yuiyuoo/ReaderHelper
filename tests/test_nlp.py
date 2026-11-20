"""Needs the spaCy models (python -m spacy download en_core_web_sm / de_core_news_sm)."""
import pytest

from backend import nlp


@pytest.mark.parametrize("lang, word, sentence, lemma, pos", [
    ("en", "banks", "The children played on the banks of the river.", "bank", "NOUN"),
    ("de", "Häuser", "Die Häuser sind alt.", "Haus", "NOUN"),
    ("de", "ging", "Er ging nach Hause.", "gehen", "VERB"),
])
def test_analyze_finds_base_form(lang, word, sentence, lemma, pos):
    got_lemma, got_pos, _gender = nlp.analyze(lang, word, sentence)
    assert (got_lemma, got_pos) == (lemma, pos)


def test_detect_lang():
    assert nlp.detect_lang("Die Kinder spielten am Ufer des Flusses.") == "de"
    assert nlp.detect_lang("The children played on the riverbank.") == "en"


def test_detect_lang_uses_book_language_for_short_text():
    assert nlp.detect_lang("Ok", hint="de-DE") == "de"
