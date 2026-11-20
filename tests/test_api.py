"""HTTP API with a temporary database (see conftest.py). No network or Ollama needed."""
import uuid

import pytest
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)

WORD = {"lang": "en", "word": "banks", "lemma": "bank",
        "sentence": "They sat on the banks.", "translation_vi": "bờ sông", "gloss_vi": "mép sông"}


def new_profile() -> dict:
    r = client.post("/api/profiles", json={"name": f"Tester {uuid.uuid4().hex[:8]}"})
    assert r.status_code == 200
    return {"X-Profile": str(r.json()["id"])}


@pytest.fixture
def profile():
    return new_profile()


def test_pages_are_served():
    for path in ("/", "/read", "/vocab", "/review"):
        assert client.get(path).status_code == 200


def test_duplicate_profile_name_is_refused():
    name = f"Dup {uuid.uuid4().hex[:8]}"
    assert client.post("/api/profiles", json={"name": name}).status_code == 200
    assert client.post("/api/profiles", json={"name": name}).status_code == 409


def test_vocab_needs_a_profile():
    assert client.get("/api/vocab").status_code == 401


def test_save_list_and_delete_word(profile):
    saved = client.post("/api/vocab", json=WORD, headers=profile).json()
    assert saved["lemma"] == "bank"
    assert [v["id"] for v in client.get("/api/vocab", headers=profile).json()] == [saved["id"]]

    assert client.delete(f"/api/vocab/{saved['id']}", headers=profile).status_code == 200
    assert client.get("/api/vocab", headers=profile).json() == []


def test_saving_same_word_twice_updates_one_entry(profile):
    client.post("/api/vocab", json=WORD, headers=profile)
    client.post("/api/vocab", json={**WORD, "translation_vi": "bờ"}, headers=profile)
    words = client.get("/api/vocab", headers=profile).json()
    assert len(words) == 1 and words[0]["translation_vi"] == "bờ"


def test_profiles_dont_see_each_others_words(profile):
    client.post("/api/vocab", json=WORD, headers=profile)
    assert client.get("/api/vocab", headers=new_profile()).json() == []


def test_review_schedules_the_word(profile):
    saved = client.post("/api/vocab", json=WORD, headers=profile).json()
    r = client.post(f"/api/vocab/{saved['id']}/review", json={"grade": "good"}, headers=profile)
    assert r.status_code == 200
    assert r.json()["interval_days"] == 1.0
    bad = client.post(f"/api/vocab/{saved['id']}/review", json={"grade": "meh"}, headers=profile)
    assert bad.status_code == 422


def test_anki_csv_export(profile):
    client.post("/api/vocab", json=WORD, headers=profile)
    r = client.get("/api/vocab.csv", headers=profile)
    assert r.headers["content-type"].startswith("text/csv")
    lines = r.content.decode("utf-8").splitlines()
    assert lines[:2] == ["#separator:comma", "#html:true"]
    assert "<b>banks</b>" in lines[2] and "bờ sông" in lines[2]


def test_book_file_paths_cannot_escape_the_folder():
    assert client.get("/books/..%2Fapp.db").status_code == 404
