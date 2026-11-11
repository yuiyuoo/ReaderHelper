"""Quick quality check for the lookup pipeline against a running server.

Usage:  python tools/eval_words.py [base_url] [-v]   (default http://127.0.0.1:8000; -v also prints
        the model's translation of the whole sentence)

Each case passes if translation_vi or sense_vi contains one of the accepted Vietnamese keywords.
Lookups use refresh=true, so the cache is bypassed (results are still written to it).
"""
import sys
import time

import httpx

KAFKA_1 = ("Er lag auf seinem panzerartig harten Rücken und sah, wenn er den Kopf ein wenig hob, "
           "seinen gewölbten, braunen, von bogenförmigen Versteifungen geteilten Bauch, auf dessen Höhe "
           "sich die Bettdecke, zum gänzlichen Niedergleiten bereit, kaum noch erhalten konnte.")
KAFKA_2 = ("Als Gregor Samsa eines Morgens aus unruhigen Träumen erwachte, fand er sich in seinem Bett "
           "zu einem ungeheueren Ungeziefer verwandelt.")

# (word, sentence, accepted keywords)
CASES = [
    ("bank", "We sat on the bank of the river and watched the boats.", ["bờ"]),
    ("bank", "She deposited the money in the bank yesterday.", ["ngân hàng"]),
    ("tired", "Alice was beginning to get very tired of sitting by her sister on the bank.", ["mệt", "chán"]),
    ("runs", "My uncle runs a small company in Hanoi.", ["điều hành", "quản lý", "kinh doanh"]),
    ("fire", "The manager decided to fire him after the meeting.", ["sa thải", "đuổi việc", "cho nghỉ"]),
    ("light", "The bag is light enough for a child to carry.", ["nhẹ"]),
    ("lag", KAFKA_1, ["nằm"]),
    ("Rücken", KAFKA_1, ["lưng"]),
    ("kaum", KAFKA_1, ["hầu như", "gần như", "khó", "vừa đủ", "suýt"]),
    ("erwachte", KAFKA_2, ["thức", "tỉnh"]),
    ("Ungeziefer", KAFKA_2, ["sâu", "bọ", "côn trùng"]),
    ("stand", "Er stand jeden Tag früh am Morgen auf.", ["dậy"]),
    ("Bank", "Die alte Frau saß auf einer Bank im Park.", ["ghế"]),
    ("Schloss", "Er steckte den Schlüssel ins Schloss und öffnete die Tür.", ["khóa", "khoá"]),
]


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    base = args[0] if args else "http://127.0.0.1:8000"
    passed = 0
    for word, sentence, ok in CASES:
        t = time.time()
        r = httpx.post(f"{base}/lookup", json={"word": word, "sentence": sentence, "refresh": True}, timeout=300).json()
        answer = f"{r.get('translation_vi', '')} / {r.get('sense_vi', '')}"
        hit = any(k in answer.lower() for k in ok)
        passed += hit
        print(f"{'PASS' if hit else 'FAIL'} {time.time() - t:4.1f}s {word:10} {r.get('lemma', ''):12} {answer}"
              + (f"   [warning: {r['warning']}]" if r.get("warning") else ""))
        if r.get("sentence_tr") and "-v" in sys.argv:
            print(f"{'':28}sentence: {r['sentence_tr']}")
    print(f"\n{passed}/{len(CASES)} passed")


if __name__ == "__main__":
    main()
