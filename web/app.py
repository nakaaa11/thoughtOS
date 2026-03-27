"""Thought OS Web Dashboard — FastAPI バックエンド"""

import json
import sys
import tempfile
import threading
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.config import load_settings
from src.db import ThoughtDB
from src.embedder import Embedder
from src.parsers.file_parser import FileParser, EXTRACTOR_MAP

settings = load_settings()
db = ThoughtDB(settings.database_url)
_embedder: Embedder | None = None

# バックグラウンド処理の状態管理
_process_status: dict = {"running": False, "progress": 0, "total": 0, "done": False, "error": None, "result": None}

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
# Import
# ──────────────────────────────────────────────
ALLOWED_EXTENSIONS = set(EXTRACTOR_MAP.keys())


@app.post("/api/import")
async def import_files(files: list[UploadFile] = File(...)):
    """ファイルをアップロードしてDBに取り込む（要約・embedding処理なし）"""
    file_parser = FileParser()
    inserted_total = 0
    skipped_total = 0
    results = []

    for upload in files:
        filename = upload.filename or "unknown"
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            results.append({"file": filename, "status": "error", "message": f"未対応の形式: {ext}"})
            continue

        content = await upload.read()
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)
        # ファイル名を元のファイル名に偽装（パーサーがファイル名を使うため）
        tmp_path_named = tmp_path.parent / filename
        try:
            tmp_path.rename(tmp_path_named)
            tmp_path = tmp_path_named
        except Exception:
            pass

        try:
            entries = file_parser.parse(tmp_path)
            inserted = 0
            skipped = 0
            for e in entries:
                entry_dict = {
                    "source_type": e.source_type,
                    "source_id": e.source_id,
                    "title": e.title,
                    "content": e.content,
                    "summary": None,
                    "category": None,
                    "tags": [],
                    "thinking_pattern": None,
                    "embedding": None,
                    "source_metadata": json.dumps(e.source_metadata),
                    "created_at": e.created_at,
                    "updated_at": e.updated_at,
                    "file_hash": e.file_hash,
                }
                if db.insert_entry(entry_dict):
                    inserted += 1
                else:
                    skipped += 1
            results.append({
                "file": filename,
                "status": "ok",
                "inserted": inserted,
                "skipped": skipped,
            })
            inserted_total += inserted
            skipped_total += skipped
        except Exception as exc:
            results.append({"file": filename, "status": "error", "message": str(exc)})
        finally:
            tmp_path.unlink(missing_ok=True)

    return {
        "inserted": inserted_total,
        "skipped": skipped_total,
        "files": results,
    }


@app.post("/api/process")
async def start_processing(background_tasks: BackgroundTasks):
    """未処理エントリの要約・カテゴリ・embedding処理をバックグラウンドで開始"""
    global _process_status
    if _process_status["running"]:
        return {"message": "already_running", **_process_status}

    unprocessed_count = len(db.get_unprocessed_entries())
    if unprocessed_count == 0:
        return {"message": "nothing_to_process", "total": 0}

    _process_status = {
        "running": True, "progress": 0, "total": unprocessed_count,
        "done": False, "error": None, "result": None,
    }
    background_tasks.add_task(_run_pipeline)
    return {"message": "started", "total": unprocessed_count}


@app.get("/api/process/status")
async def process_status():
    return _process_status


def _run_pipeline():
    global _process_status
    try:
        from src.pipeline import Pipeline
        pipeline = Pipeline(settings)

        # 進捗をモニタリングするためにsummarizer をラップ
        original_summarize = pipeline.summarizer.summarize

        def tracked_summarize(raw):
            result = original_summarize(raw)
            _process_status["progress"] = _process_status.get("progress", 0) + 1
            return result

        pipeline.summarizer.summarize = tracked_summarize

        result = pipeline.run_process_unprocessed()
        _process_status.update({
            "running": False, "done": True, "result": result,
            "progress": _process_status["total"],
        })
    except Exception as e:
        _process_status.update({"running": False, "done": True, "error": str(e)})


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
