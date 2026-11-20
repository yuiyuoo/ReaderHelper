"""The model's raw text is messy; parse_response must turn it into clean fields or refuse it."""
import pytest

from backend import llm


def test_mark_word_marks_first_whole_word_only():
    assert llm.mark_word("bank", "The bank by the bank.") == "The [[bank]] by the bank."
    assert llm.mark_word("an", "A banana.") == "A banana."  # not inside another word


def test_mark_word_keeps_already_marked_sentence():
    s = "Sie [[hört]] damit [[auf]]."
    assert llm.mark_word("hört", s) == s


def test_parse_strips_think_block_and_code_fence():
    raw = ('<think>let me think</think>```json\n'
           '{"sentence": "Bọn trẻ chơi trên bờ sông.", "translation": "bờ sông", "gloss": "g", "sense": "bờ"}\n```')
    out = llm.parse_response(raw)
    assert out["translation_vi"] == "bờ sông"
    assert out["sentence_tr"] == "Bọn trẻ chơi trên bờ sông."
    assert set(out) == {"translation_vi", "gloss_vi", "sense_vi", "sentence_tr"}


def test_parse_normalises_to_nfc():
    decomposed = "bờ"  # "bờ" written with combining marks
    out = llm.parse_response(f'{{"translation": "{decomposed}", "gloss": "g", "sense": "s"}}')
    assert out["translation_vi"] == "bờ"


@pytest.mark.parametrize("n, expected", [(2, 2), (5, None), ("x", None)])
def test_parse_sense_number_is_checked(n, expected):
    raw = f'{{"translation": "t", "gloss": "g", "sense": "s", "sense_no": "{n}"}}'
    assert llm.parse_response(raw, n_senses=3)["sense_no"] == expected


@pytest.mark.parametrize("raw", [
    "no json here",
    '{"translation": "t", "gloss": "g"}',                   # missing key
    '{"translation": "", "gloss": "g", "sense": "s"}',      # empty translation
    '{"translation": "银行", "gloss": "g", "sense": "s"}',  # Chinese characters
])
def test_parse_rejects_unusable_answers(raw):
    with pytest.raises(ValueError):
        llm.parse_response(raw)


def test_prompt_marks_the_word_in_its_sentence():
    prompt = llm.build_prompt("en", "banks", "They sat on the banks.", lemma="bank")
    assert "Target word: banks (base form: bank)" in prompt
    assert "Sentence: They sat on the [[banks]]." in prompt


def test_cache_variant_changes_with_target_language():
    assert llm.cache_variant("vi") != llm.cache_variant("de")
