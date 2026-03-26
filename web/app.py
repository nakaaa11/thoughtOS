"""Thought OS Web Dashboard — FastAPI バックエンド"""

import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.config import load_settings
from src.db import ThoughtDB
from src.embedder import Embedder

settings = load_settings()
db = ThoughtDB(settings.database_url)
_embedder: Embedder | None = None

STATIC_DIR = Path(__file__).parent / "static"


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder(settings.voyage_api_key, settings.voyage_model)
    return _embedder


app = FastAPI(title="Thought OS", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def root():
    return FileResponse(str(STATIC_DIR / "index.html"))


# ──────────────────────────────────────────────
# Stats
# ──────────────────────────────────────────────
@app.get("/api/stats")
async def get_stats():
    conn = db._get_conn()

    rows = conn.execute(
        "SELECT source_type, COUNT(*) as total, COUNT(summary) as processed "
        "FROM thought_entries GROUP BY source_type ORDER BY total DESC"
    ).fetchall()
    by_source = [{"source_type": r[0], "total": r[1], "processed": r[2]} for r in rows]

    date_range = conn.execute(
        "SELECT MIN(created_at)::date, MAX(created_at)::date FROM thought_entries"
    ).fetchone()

    cat_rows = conn.execute(
        "SELECT category, COUNT(*) FROM thought_entries WHERE category IS NOT NULL "
        "GROUP BY category ORDER BY COUNT(*) DESC LIMIT 10"
    ).fetchall()

    session_count = conn.execute("SELECT COUNT(*) FROM thinking_sessions").fetchone()[0]

    return {
        "total": sum(r["total"] for r in by_source),
        "by_source": by_source,
        "date_range": {
            "start": str(date_range[0]) if date_range[0] else None,
            "end": str(date_range[1]) if date_range[1] else None,
        },
        "top_categories": [{"category": r[0], "count": r[1]} for r in cat_rows],
        "session_count": session_count,
    }


# ──────────────────────────────────────────────
# Tags
# ──────────────────────────────────────────────
@app.get("/api/tags")
async def get_tags(limit: int = 60):
    return db.get_all_tags()[:limit]


@app.get("/api/tag/{tag}")
async def browse_tag(tag: str, limit: int = 30):
    results = db.browse_by_tag(tag, limit=limit)
    return _serialize_entries(results)


# ──────────────────────────────────────────────
# Search
# ──────────────────────────────────────────────
class SearchRequest(BaseModel):
    query: str
    source_type: Optional[str] = None
    category: Optional[str] = None
    limit: int = 20


@app.post("/api/search")
async def search(req: SearchRequest):
    seen_ids: set[str] = set()
    results: list[dict] = []

    # ベクトル検索
    try:
        emb = get_embedder().embed(req.query)
        for r in db.search_by_similarity(emb, limit=req.limit, source_type=req.source_type):
            rid = str(r.get("id", ""))
            if rid not in seen_ids:
                seen_ids.add(rid)
                results.append(r)
    except Exception:
        pass

    # キーワード検索
    for r in db.search_by_keyword(
        req.query, limit=req.limit, source_type=req.source_type, category=req.category
    ):
        rid = str(r.get("id", ""))
        if rid not in seen_ids:
            seen_ids.add(rid)
            results.append(r)

    return _serialize_entries(results[: req.limit])


# ──────────────────────────────────────────────
# Entries
# ──────────────────────────────────────────────
@app.get("/api/entries/{entry_id}")
async def get_entry(entry_id: str):
    entry = db.get_entry(entry_id)
    if not entry:
        raise HTTPException(404, "Entry not found")
    return _serialize_entry(entry)


@app.get("/api/period")
async def browse_period(
    start: str = Query(...),
    end: str = Query(...),
    source_type: Optional[str] = None,
    limit: int = 50,
):
    results = db.browse_by_period(start, end, limit=limit)
    if source_type:
        results = [r for r in results if r.get("source_type") == source_type]
    return _serialize_entries(results)


# ──────────────────────────────────────────────
# Sessions
# ──────────────────────────────────────────────
@app.get("/api/sessions")
async def get_sessions(limit: int = 20):
    conn = db._get_conn()
    rows = conn.execute(
        "SELECT id, topic, narrative, sources, tags, timeframe_start, timeframe_end "
        "FROM thinking_sessions ORDER BY timeframe_start DESC LIMIT %s",
        (limit,),
    ).fetchall()
    return [
        {
            "id": str(r[0]),
            "topic": r[1],
            "narrative": r[2],
            "sources": r[3],
            "tags": r[4],
            "timeframe_start": r[5].isoformat() if r[5] else None,
            "timeframe_end": r[6].isoformat() if r[6] else None,
        }
        for r in rows
    ]


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────
def _serialize_entries(entries: list[dict]) -> list[dict]:
    return [_serialize_entry(e) for e in entries]


def _serialize_entry(e: dict) -> dict:
    result = {}
    for k, v in e.items():
        if k == "embedding":
            continue
        if hasattr(v, "isoformat"):
            result[k] = v.isoformat()
        elif k == "thinking_pattern" and isinstance(v, str):
            try:
                result[k] = json.loads(v)
            except Exception:
                result[k] = v
        else:
            result[k] = v
    return result
