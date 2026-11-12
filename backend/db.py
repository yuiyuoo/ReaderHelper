"""SQLite storage: lookup cache + saved vocabulary."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "app.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as conn:
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(lookup_cache)")]
        if cols and "variant" not in cols:
            # Old cache had no model/prompt column; it's only a cache, so drop and rebuild it.
            conn.execute("DROP TABLE lookup_cache")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS lookup_cache (
                lang TEXT NOT NULL,
                lemma TEXT NOT NULL,
                sentence TEXT NOT NULL,
                variant TEXT NOT NULL,          -- model + prompt version that produced the answer
                result TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (lang, lemma, sentence, variant)
            );
            CREATE TABLE IF NOT EXISTS vocab (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lang TEXT NOT NULL,
                word TEXT NOT NULL,
                lemma TEXT NOT NULL,
                sentence TEXT NOT NULL,
                translation_vi TEXT NOT NULL DEFAULT '',
                gloss_vi TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                target_lang TEXT NOT NULL DEFAULT 'vi'  -- language of translation/gloss
            );
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL UNIQUE,      -- file in books/
                title TEXT NOT NULL,                -- display name (editable)
                author TEXT NOT NULL DEFAULT '',
                language TEXT NOT NULL DEFAULT '',
                cover TEXT NOT NULL DEFAULT '',     -- file in covers/, '' = default icon
                added_at TEXT NOT NULL,
                opened_at TEXT                      -- last time it was opened in the reader
            );
            """
        )
        # Columns added after the first version; older databases get them here (existing rows keep
        # working: meanings were Vietnamese, no book/position known, never reviewed).
        _add_columns(conn, "vocab", {
            "target_lang": "TEXT NOT NULL DEFAULT 'vi'",   # language of translation/gloss
            "sentence_tr": "TEXT NOT NULL DEFAULT ''",     # translation of the whole sentence
            "book_id": "INTEGER",                          # book it was saved from (NULL = unknown)
            "cfi": "TEXT NOT NULL DEFAULT ''",             # exact position in that book (EPUB CFI)
            # spaced repetition (see srs.py); due_at NULL = new word, not reviewed yet
            "due_at": "TEXT",
            "interval_days": "REAL NOT NULL DEFAULT 0",
            "ease": "REAL NOT NULL DEFAULT 2.5",
            "reps": "INTEGER NOT NULL DEFAULT 0",
            "lapses": "INTEGER NOT NULL DEFAULT 0",
            "reviewed_at": "TEXT",
        })


def _add_columns(conn, table: str, columns: dict) -> None:
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    for name, decl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_cached(lang: str, lemma: str, sentence: str, variant: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT result FROM lookup_cache WHERE lang=? AND lemma=? AND sentence=? AND variant=?",
            (lang, lemma, sentence, variant),
        ).fetchone()
    return json.loads(row["result"]) if row else None


def put_cached(lang: str, lemma: str, sentence: str, variant: str, result: dict) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO lookup_cache (lang, lemma, sentence, variant, result, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (lang, lemma, sentence, variant, json.dumps(result, ensure_ascii=False), _now()),
        )


def add_vocab(item: dict) -> dict:
    """Insert a word, or update the existing entry for the same lemma in the same sentence
    with the same meaning language."""
    target = item.get("target_lang", "vi")
    with _conn() as conn:
        existing = conn.execute(
            "SELECT id FROM vocab WHERE lang=? AND lemma=? AND sentence=? AND target_lang=?",
            (item["lang"], item["lemma"], item["sentence"], target),
        ).fetchone()
        if existing:
            # Saving again refreshes the meaning; the review history is kept.
            conn.execute(
                "UPDATE vocab SET word=?, translation_vi=?, gloss_vi=?, sentence_tr=?,"
                " book_id=COALESCE(?, book_id), cfi=CASE WHEN ?='' THEN cfi ELSE ? END WHERE id=?",
                (item["word"], item.get("translation_vi", ""), item.get("gloss_vi", ""),
                 item.get("sentence_tr", ""), item.get("book_id"), item.get("cfi", ""), item.get("cfi", ""),
                 existing["id"]),
            )
            return get_vocab(existing["id"])
        cur = conn.execute(
            "INSERT INTO vocab (lang, word, lemma, sentence, translation_vi, gloss_vi, created_at, target_lang,"
            " sentence_tr, book_id, cfi) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                item["lang"], item["word"], item["lemma"], item["sentence"],
                item.get("translation_vi", ""), item.get("gloss_vi", ""), _now(), target,
                item.get("sentence_tr", ""), item.get("book_id"), item.get("cfi", ""),
            ),
        )
    return get_vocab(cur.lastrowid)


VOCAB_SELECT = "SELECT v.*, b.title AS book_title FROM vocab v LEFT JOIN books b ON b.id = v.book_id"


def get_vocab(vocab_id: int) -> dict | None:
    with _conn() as conn:
        row = conn.execute(f"{VOCAB_SELECT} WHERE v.id=?", (vocab_id,)).fetchone()
    return dict(row) if row else None


def list_vocab(book_id: int | None = None) -> list[dict]:
    """All saved words (newest first), or only those saved from one book."""
    where, args = ("WHERE v.book_id=?", (book_id,)) if book_id is not None else ("", ())
    with _conn() as conn:
        rows = conn.execute(f"{VOCAB_SELECT} {where} ORDER BY v.created_at DESC, v.id DESC", args).fetchall()
    return [dict(r) for r in rows]


# ---------- review (spaced repetition) ----------

def _book_filter(book_id: int | None) -> tuple[str, tuple]:
    return ("AND v.book_id=?", (book_id,)) if book_id is not None else ("", ())


def review_queue(book_id: int | None = None, limit: int = 50) -> list[dict]:
    """Words to review now: due ones first (most overdue first), then new ones (oldest saved first)."""
    flt, args = _book_filter(book_id)
    with _conn() as conn:
        rows = conn.execute(
            f"{VOCAB_SELECT} WHERE (v.due_at IS NULL OR v.due_at <= ?) {flt}"
            " ORDER BY v.due_at IS NULL, v.due_at, v.created_at, v.id LIMIT ?",
            (_now(), *args, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def review_counts(book_id: int | None = None) -> dict:
    """{due, new, total, next_due}: due = reviewed before and due now; new = never reviewed."""
    flt, args = _book_filter(book_id)
    now = _now()
    with _conn() as conn:
        r = conn.execute(
            "SELECT"
            " SUM(CASE WHEN v.due_at IS NOT NULL AND v.due_at <= ? THEN 1 ELSE 0 END) AS due,"
            " SUM(CASE WHEN v.due_at IS NULL THEN 1 ELSE 0 END) AS new,"
            " COUNT(*) AS total,"
            " MIN(CASE WHEN v.due_at > ? THEN v.due_at END) AS next_due"
            f" FROM vocab v WHERE 1=1 {flt}",
            (now, now, *args),
        ).fetchone()
    return {"due": r["due"] or 0, "new": r["new"] or 0, "total": r["total"], "next_due": r["next_due"]}


def save_review(vocab_id: int, fields: dict) -> dict | None:
    allowed = ("due_at", "interval_days", "ease", "reps", "lapses", "reviewed_at")
    fields = {k: v for k, v in fields.items() if k in allowed}
    with _conn() as conn:
        sets = ", ".join(f"{k}=?" for k in fields)  # keys come from the whitelist above
        conn.execute(f"UPDATE vocab SET {sets} WHERE id=?", (*fields.values(), vocab_id))
    return get_vocab(vocab_id)


EDITABLE_VOCAB_FIELDS = ("translation_vi", "gloss_vi", "sentence")


def update_vocab(vocab_id: int, changes: dict) -> dict | None:
    """Update the editable fields given in `changes`; return the updated row, or None if not found."""
    fields = {k: v for k, v in changes.items() if k in EDITABLE_VOCAB_FIELDS}
    with _conn() as conn:
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)  # keys come from the whitelist above
            conn.execute(f"UPDATE vocab SET {sets} WHERE id=?", (*fields.values(), vocab_id))
    return get_vocab(vocab_id)


def delete_vocab(vocab_id: int) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM vocab WHERE id=?", (vocab_id,))
    return cur.rowcount > 0


# ---------- books (library) ----------

BOOK_FIELDS = ("title", "author", "cover")


def list_books() -> list[dict]:
    """Most recently opened first, then most recently added. Includes how many words were saved from it."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT books.*, (SELECT COUNT(*) FROM vocab WHERE vocab.book_id = books.id) AS word_count"
            " FROM books ORDER BY opened_at IS NULL, opened_at DESC, added_at DESC, id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_book(book_id: int) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    return dict(row) if row else None


def add_book(filename: str, title: str, author: str = "", language: str = "", cover: str = "") -> dict:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO books (filename, title, author, language, cover, added_at) VALUES (?,?,?,?,?,?)",
            (filename, title, author, language, cover, _now()),
        )
        row = conn.execute("SELECT * FROM books WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


def update_book(book_id: int, changes: dict) -> dict | None:
    fields = {k: v for k, v in changes.items() if k in BOOK_FIELDS}
    with _conn() as conn:
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)  # keys come from the whitelist above
            conn.execute(f"UPDATE books SET {sets} WHERE id=?", (*fields.values(), book_id))
        row = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    return dict(row) if row else None


def mark_book_opened(book_id: int) -> None:
    with _conn() as conn:
        conn.execute("UPDATE books SET opened_at=? WHERE id=?", (_now(), book_id))


def delete_book(book_id: int) -> bool:
    with _conn() as conn:
        # Saved words stay, they just no longer point into the removed book.
        conn.execute("UPDATE vocab SET book_id=NULL, cfi='' WHERE book_id=?", (book_id,))
        cur = conn.execute("DELETE FROM books WHERE id=?", (book_id,))
    return cur.rowcount > 0
