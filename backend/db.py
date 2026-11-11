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
            """
        )
        # Older databases: add the column; existing entries were all Vietnamese.
        if "target_lang" not in [r["name"] for r in conn.execute("PRAGMA table_info(vocab)")]:
            conn.execute("ALTER TABLE vocab ADD COLUMN target_lang TEXT NOT NULL DEFAULT 'vi'")


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
            conn.execute(
                "UPDATE vocab SET word=?, translation_vi=?, gloss_vi=? WHERE id=?",
                (item["word"], item.get("translation_vi", ""), item.get("gloss_vi", ""), existing["id"]),
            )
            return dict(conn.execute("SELECT * FROM vocab WHERE id=?", (existing["id"],)).fetchone())
        cur = conn.execute(
            "INSERT INTO vocab (lang, word, lemma, sentence, translation_vi, gloss_vi, created_at, target_lang)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                item["lang"], item["word"], item["lemma"], item["sentence"],
                item.get("translation_vi", ""), item.get("gloss_vi", ""), _now(), target,
            ),
        )
        row = conn.execute("SELECT * FROM vocab WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


def list_vocab() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM vocab ORDER BY created_at DESC, id DESC").fetchall()
    return [dict(r) for r in rows]


EDITABLE_VOCAB_FIELDS = ("translation_vi", "gloss_vi", "sentence")


def update_vocab(vocab_id: int, changes: dict) -> dict | None:
    """Update the editable fields given in `changes`; return the updated row, or None if not found."""
    fields = {k: v for k, v in changes.items() if k in EDITABLE_VOCAB_FIELDS}
    with _conn() as conn:
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)  # keys come from the whitelist above
            conn.execute(f"UPDATE vocab SET {sets} WHERE id=?", (*fields.values(), vocab_id))
        row = conn.execute("SELECT * FROM vocab WHERE id=?", (vocab_id,)).fetchone()
    return dict(row) if row else None


def delete_vocab(vocab_id: int) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM vocab WHERE id=?", (vocab_id,))
    return cur.rowcount > 0
