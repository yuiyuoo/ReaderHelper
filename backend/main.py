"""FastAPI app: serves the frontend, the library, /lookup, /translate and the vocab API."""
import asyncio
import csv
import io
import logging
import re
import time
import unicodedata
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from . import books, db, llm, nlp, srs  # noqa: E402  (after load_dotenv so config is read)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("readerhelper")

FRONTEND = ROOT / "frontend"
BOOKS = ROOT / "books"
BOOKS.mkdir(exist_ok=True)
COVERS = ROOT / "covers"  # cover pictures (from the EPUB or uploaded)
COVERS.mkdir(exist_ok=True)

app = FastAPI(title="ReaderHelper")
db.init_db()


@app.middleware("http")
async def no_stale_frontend(request, call_next):
    # Make browsers revalidate HTML/JS/CSS so code updates are picked up without a hard reload.
    response = await call_next(request)
    if request.url.path in ("/", "/read", "/vocab", "/review") or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# ---------- pages & books ----------

@app.get("/", include_in_schema=False)
def library_page():
    return FileResponse(FRONTEND / "library.html", media_type="text/html; charset=utf-8")


@app.get("/read", include_in_schema=False)
def reader_page():
    return FileResponse(FRONTEND / "index.html", media_type="text/html; charset=utf-8")


@app.get("/vocab", include_in_schema=False)
def vocab_page():
    return FileResponse(FRONTEND / "vocab.html", media_type="text/html; charset=utf-8")


@app.get("/review", include_in_schema=False)
def review_page():
    return FileResponse(FRONTEND / "review.html", media_type="text/html; charset=utf-8")


# ---------- library ----------

def _inside(folder: Path, name: str) -> Path | None:
    """Resolve `name` inside `folder`, refusing anything that escapes it."""
    path = (folder / name).resolve()
    return path if path.parent == folder.resolve() and path.is_file() else None


def _remove_cover(name: str) -> None:
    if name and (path := _inside(COVERS, name)):
        path.unlink(missing_ok=True)


def _save_cover(book_id: int, data: bytes, ext: str) -> str:
    # New name on every change, so browsers never show a cached old picture.
    name = f"{book_id}-{int(time.time() * 1000)}.{ext}"
    (COVERS / name).write_bytes(data)
    return name


def _register(filename: str) -> dict:
    """Add a file from books/ to the library, using the EPUB's own title, author, language and cover."""
    meta = books.read_epub_meta(BOOKS / filename)
    fallback = re.sub(r"[_\-]+", " ", Path(filename).stem).strip() or filename
    book = db.add_book(filename, meta["title"] or fallback, meta["author"], meta["language"])
    if meta["cover"]:
        book = db.update_book(book["id"], {"cover": _save_cover(book["id"], *meta["cover"])})
    return book


def _sync_library() -> None:
    """Books dropped into books/ by hand appear in the library; rows whose file is gone are removed."""
    files = {p.name for p in BOOKS.glob("*.epub")}
    known = {b["filename"]: b for b in db.list_books()}
    for name in sorted(files - known.keys()):
        _register(name)
    for name in known.keys() - files:
        _remove_cover(known[name]["cover"])
        db.delete_book(known[name]["id"])


def _public(book: dict) -> dict:
    return {**book, "cover_url": f"/covers/{book['cover']}" if book["cover"] else None,
            "file_url": f"/books/{quote(book['filename'])}"}


def _book_or_404(book_id: int) -> dict:
    book = db.get_book(book_id)
    if not book:
        raise HTTPException(404, "Book not found")
    return book


@app.get("/api/books")
def list_books():
    _sync_library()
    return [_public(b) for b in db.list_books()]


@app.get("/api/books/{book_id}")
def get_book_info(book_id: int):
    return _public(_book_or_404(book_id))


@app.post("/api/books")
async def upload_book(file: UploadFile = File(...)):
    name = Path(file.filename or "book.epub").name
    if not name.lower().endswith(".epub"):
        raise HTTPException(400, "Please choose an .epub file.")
    data = await file.read()
    if not data.startswith(b"PK"):
        raise HTTPException(400, "This file is not a valid EPUB.")
    stem = re.sub(r"[^\w.\- ]", "_", Path(name).stem) or "book"
    name, n = f"{stem}.epub", 2
    while (BOOKS / name).exists():  # never overwrite another book
        name, n = f"{stem}-{n}.epub", n + 1
    (BOOKS / name).write_bytes(data)
    return _public(_register(name))


class BookUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    author: str | None = Field(None, max_length=200)


@app.patch("/api/books/{book_id}")
def edit_book(book_id: int, changes: BookUpdate):
    _book_or_404(book_id)
    fields = {k: v.strip() for k, v in changes.model_dump(exclude_none=True).items()}
    if fields.get("title") == "":
        raise HTTPException(400, "The title can't be empty.")
    return _public(db.update_book(book_id, fields))


@app.post("/api/books/{book_id}/cover")
async def upload_cover(book_id: int, file: UploadFile = File(...)):
    book = _book_or_404(book_id)
    data = await file.read()
    if len(data) > books.MAX_COVER_BYTES:
        raise HTTPException(400, "The picture is too large (max. 5 MB).")
    ext = books.sniff_image(data)
    if not ext:
        raise HTTPException(400, "Please choose a JPG, PNG, GIF or WebP picture.")
    _remove_cover(book["cover"])
    return _public(db.update_book(book_id, {"cover": _save_cover(book_id, data, ext)}))


@app.delete("/api/books/{book_id}/cover")
def remove_cover(book_id: int):
    book = _book_or_404(book_id)
    _remove_cover(book["cover"])
    return _public(db.update_book(book_id, {"cover": ""}))


@app.post("/api/books/{book_id}/opened")
def book_opened(book_id: int):
    _book_or_404(book_id)
    db.mark_book_opened(book_id)
    return {"ok": True}


@app.delete("/api/books/{book_id}")
def delete_book(book_id: int):
    """Removes the book file, its cover and its library entry. Saved words are kept."""
    book = _book_or_404(book_id)
    if path := _inside(BOOKS, book["filename"]):
        path.unlink(missing_ok=True)
    _remove_cover(book["cover"])
    db.delete_book(book_id)
    return {"ok": True}


@app.get("/books/{name}")
def get_book_file(name: str):
    path = _inside(BOOKS, name)
    if not path:
        raise HTTPException(404, "Book not found")
    return FileResponse(path, media_type="application/epub+zip")


@app.get("/covers/{name}")
def get_cover(name: str):
    path = _inside(COVERS, name)
    if not path:
        raise HTTPException(404, "Cover not found")
    # Cover names change whenever the picture changes, so they can be cached for a long time.
    return FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})


# ---------- lookup ----------

class LookupIn(BaseModel):
    word: str = Field(min_length=1, max_length=100)
    sentence: str = Field(min_length=1, max_length=2000)
    lang_hint: str | None = None  # optional: book language from EPUB metadata
    refresh: bool = False         # skip the cache and ask the model again
    quick: bool = False           # only language, lemma and dictionary (fast); the model answer follows
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
    base = {
        "lang": lang, "word": word, "lemma": lemma, "sentence": sentence,
        "definition": senses[0] if senses else "", "senses": senses,
        "definition_note": dict_err, "target_lang": q.target,
    }
    if q.quick:
        # Step 1 of two: show this at once; the client then asks again without `quick` for the model's
        # answer (the dictionary result is cached in memory, so that call doesn't fetch it again).
        return {**base, "pending": True, "cached": False}

    llm_result = await _run_llm(lang, word, sentence, lemma, senses, q.target)

    result = {**base, "sense_no": None, "translation_vi": "", "gloss_vi": "", "sense_vi": "",
              "sentence_tr": "", "warning": None}
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


@app.post("/api/warmup")
async def warmup():
    """Load the model into memory in the background (the reader calls this when a book opens),
    so the first word lookup doesn't also have to wait for the model to load. The spaCy models are
    loaded too: that takes a few seconds on first use and would otherwise delay the quick step."""
    asyncio.create_task(llm.warmup())
    for lang in nlp.MODELS:
        asyncio.create_task(asyncio.to_thread(nlp.get_nlp, lang))
    return {"ok": True}


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
    sentence_tr: str = Field("", max_length=2000)  # translation of the whole sentence
    book_id: int | None = None                      # where it was saved, for "Open in book"
    cfi: str = Field("", max_length=1000)


@app.post("/api/vocab")
def save_vocab(item: VocabIn):
    data = item.model_dump()
    if data["book_id"] is not None and not db.get_book(data["book_id"]):
        data["book_id"], data["cfi"] = None, ""  # unknown book: keep the word, drop the link
    return db.add_vocab(data)


@app.get("/api/vocab")
def get_vocab(book_id: int | None = None):
    return db.list_vocab(book_id)


# ---------- review (spaced repetition) ----------

@app.get("/api/review")
def get_review(book_id: int | None = None, limit: int = 50):
    """Words to review now, each with what the four answers would mean ("10 min", "3 days", …)."""
    cards = [{**c, "preview": srs.preview(c)} for c in db.review_queue(book_id, max(1, min(limit, 200)))]
    return {"cards": cards, "counts": db.review_counts(book_id)}


@app.get("/api/review/counts")
def get_review_counts(book_id: int | None = None):
    return db.review_counts(book_id)


class ReviewIn(BaseModel):
    grade: Literal["again", "hard", "good", "easy"]


@app.post("/api/vocab/{vocab_id}/review")
def review_word(vocab_id: int, answer: ReviewIn):
    card = db.get_vocab(vocab_id)
    if not card:
        raise HTTPException(404, "Not found")
    updated = db.save_review(vocab_id, srs.schedule(card, answer.grade))
    return {**updated, "preview": srs.preview(updated)}  # for showing it again in the same session


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
