"""FastAPI app: serves the frontend, books, /lookup and the vocab API."""
import asyncio
import csv
import io
import logging
import re
import unicodedata
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from . import db, llm, nlp  # noqa: E402  (after load_dotenv so config is read)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("readerhelper")

FRONTEND = ROOT / "frontend"
BOOKS = ROOT / "books"
BOOKS.mkdir(exist_ok=True)

app = FastAPI(title="ReaderHelper")
db.init_db()


@app.middleware("http")
async def no_stale_frontend(request, call_next):
    # Make browsers revalidate HTML/JS/CSS so code updates are picked up without a hard reload.
    response = await call_next(request)
    if request.url.path in ("/", "/vocab") or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# ---------- pages & books ----------

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(FRONTEND / "index.html", media_type="text/html; charset=utf-8")


@app.get("/vocab", include_in_schema=False)
def vocab_page():
    return FileResponse(FRONTEND / "vocab.html", media_type="text/html; charset=utf-8")


@app.get("/api/books")
def list_books():
    return sorted(p.name for p in BOOKS.glob("*.epub"))


@app.get("/books/{name}")
def get_book(name: str):
    path = (BOOKS / name).resolve()
    if path.parent != BOOKS.resolve() or not path.is_file():
        raise HTTPException(404, "Book not found")
    return FileResponse(path, media_type="application/epub+zip")


@app.post("/api/books")
async def upload_book(file: UploadFile = File(...)):
    name = Path(file.filename or "book.epub").name
    if not name.lower().endswith(".epub"):
        raise HTTPException(400, "Please upload an .epub file")
    name = re.sub(r"[^\w.\- ]", "_", name)
    (BOOKS / name).write_bytes(await file.read())
    return {"name": name}


# ---------- lookup ----------

class LookupIn(BaseModel):
    word: str = Field(min_length=1, max_length=100)
    sentence: str = Field(min_length=1, max_length=2000)
    lang_hint: str | None = None  # optional: book language from EPUB metadata
    refresh: bool = False         # skip the cache and ask the model again
    target: Literal["vi", "en", "de"] = "vi"  # language of the meaning/translation


@app.post("/lookup")
async def lookup(q: LookupIn):
    word = q.word.strip()
    sentence = " ".join(q.sentence.split())
    lang = nlp.detect_lang(sentence, q.lang_hint)
    try:
        sentence = await asyncio.to_thread(nlp.pick_sentence, lang, word, sentence)
        lemma, pos = await asyncio.to_thread(nlp.lemmatize, lang, word, sentence)
    except OSError:
        raise HTTPException(500, f"spaCy model for '{lang}' is missing. Run `python -m spacy download {nlp.MODELS[lang]}`.")

    variant = llm.cache_variant(q.target)
    cached = None if q.refresh else db.get_cached(lang, lemma, sentence, variant)
    if cached:
        return {**cached, "word": word, "cached": True}

    # Dictionary first: the model chooses among its numbered senses (more reliable than letting a
    # small model guess). A failure in either step doesn't affect the other.
    senses, dict_err = await nlp.fetch_senses(lang, lemma, word, pos)
    llm_result = await _run_llm(lang, word, sentence, lemma, senses, q.target)

    result = {
        "lang": lang,
        "word": word,
        "lemma": lemma,
        "sentence": sentence,
        "definition": senses[0] if senses else "",
        "senses": senses,
        "sense_no": None,
        "translation_vi": "",
        "gloss_vi": "",
        "sense_vi": "",
        "warning": None,
        "definition_note": dict_err,
        "target_lang": q.target,
    }
    if isinstance(llm_result, dict):
        result.update(llm_result)
        if result["sense_no"]:
            result["definition"] = senses[result["sense_no"] - 1]
        db.put_cached(lang, lemma, sentence, variant, result)
    else:
        result["warning"] = llm_result
    return {**result, "cached": False}


# ---------- passage translation (selected text longer than a short phrase) ----------

MAX_PASSAGE_CHARS = 600  # ~3-4 sentences; also keeps us far from "sending the whole book"
PASSAGE_KEY = "[passage]"  # lemma placeholder so passages can share the lookup cache table


class TranslateIn(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    lang_hint: str | None = None
    refresh: bool = False
    target: Literal["vi", "en", "de"] = "vi"


@app.post("/translate")
async def translate(q: TranslateIn):
    text = " ".join(q.text.split())
    if len(text) > MAX_PASSAGE_CHARS:
        raise HTTPException(
            400, f"The selection is too long ({len(text)} characters). "
                 f"Select at most about {MAX_PASSAGE_CHARS} characters (3–4 sentences)."
        )
    lang = nlp.detect_lang(text, q.lang_hint)
    variant = llm.cache_variant(q.target)
    cached = None if q.refresh else db.get_cached(lang, PASSAGE_KEY, text, variant)
    if cached:
        return {**cached, "cached": True}

    result = {"lang": lang, "target_lang": q.target, "text": text, "translation_vi": "", "notes": [],
              "warning": None}
    try:
        result.update(await llm.translate_passage(lang, text, q.target))
        db.put_cached(lang, PASSAGE_KEY, text, variant, result)
    except llm.LLMError as e:
        result["warning"] = str(e)
    except Exception as e:  # never crash the request because of the model step
        log.exception("Passage translation failed")
        result["warning"] = f"Translation unavailable: {e}"
    return {**result, "cached": False}


async def _run_llm(lang, word, sentence, lemma, senses, target) -> dict | str:
    """Return the model's fields, or a warning string if the step failed."""
    try:
        return await llm.explain(lang, word, sentence, lemma, senses, target)
    except llm.LLMError as e:
        return str(e)
    except Exception as e:  # never let the LLM step break the lookup
        log.exception("LLM step failed")
        return f"Vietnamese explanation unavailable: {e}"


# ---------- vocab ----------

class VocabIn(BaseModel):
    lang: str
    word: str
    lemma: str
    sentence: str
    translation_vi: str = ""
    gloss_vi: str = ""
    target_lang: Literal["vi", "en", "de"] = "vi"


@app.post("/api/vocab")
def save_vocab(item: VocabIn):
    return db.add_vocab(item.model_dump())


@app.get("/api/vocab")
def get_vocab():
    return db.list_vocab()


class VocabUpdate(BaseModel):
    translation_vi: str | None = Field(None, max_length=500)
    gloss_vi: str | None = Field(None, max_length=1000)
    sentence: str | None = Field(None, min_length=1, max_length=2000)


@app.patch("/api/vocab/{vocab_id}")
def edit_vocab(vocab_id: int, changes: VocabUpdate):
    # Only fields that were sent are changed; text is trimmed and normalised to NFC like model output.
    fields = {k: unicodedata.normalize("NFC", v.strip())
              for k, v in changes.model_dump(exclude_none=True).items()}
    if fields.get("sentence") == "":
        raise HTTPException(400, "The sentence can't be empty.")
    row = db.update_vocab(vocab_id, fields)
    if row is None:
        raise HTTPException(404, "Not found")
    return row


@app.delete("/api/vocab/{vocab_id}")
def remove_vocab(vocab_id: int):
    if not db.delete_vocab(vocab_id):
        raise HTTPException(404, "Not found")
    return {"ok": True}


@app.get("/api/vocab.csv")
def export_vocab():
    buf = io.StringIO()
    # Anki file headers (Anki 2.1.55+): comma-separated, fields contain HTML.
    buf.write("#separator:comma\n#html:true\n")
    w = csv.writer(buf, lineterminator="\n")
    for v in db.list_vocab():
        front = f"<b>{v['word']}</b><br><i>{v['sentence']}</i>"
        back = f"{v['translation_vi']}<br>{v['gloss_vi']}"
        w.writerow([front, back])
    return Response(
        buf.getvalue().encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="vocab_anki.csv"'},
    )


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
