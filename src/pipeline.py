import json
import time
from pathlib import Path

from .config import Settings
from .db import ThoughtDB
from .embedder import Embedder
from .claude_client import ClaudeClient
from .parsers.base import RawEntry
from .parsers.claude_parser import ClaudeParser
from .parsers.google_search_parser import GoogleSearchParser
from .parsers.google_browse_parser import GoogleBrowseParser
from .processors.categorizer import Categorizer
from .processors.summarizer import Summarizer
from .processors.pattern_extractor import PatternExtractor
from .processors.session_builder import SessionBuilder


class Pipeline:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = ThoughtDB(settings.database_url)
        self.claude = ClaudeClient(settings.anthropic_api_key, settings.claude_model)
        self.embedder = Embedder(settings.voyage_api_key, settings.voyage_model)
        self.categorizer = Categorizer(self.claude, settings.batch_size)
        self.summarizer = Summarizer(self.claude)
        self.pattern_extractor = PatternExtractor(
            self.claude, settings.min_turns_for_analysis
        )
        self.session_builder = SessionBuilder(
            self.claude,
            self.embedder,
            settings.session_time_window_minutes,
            settings.session_similarity_threshold,
        )
        self.parsers = {
            "claude": ClaudeParser(),
            "google_search": GoogleSearchParser(settings.session_time_window_minutes),
            "google_browse": GoogleBrowseParser(settings.session_time_window_minutes),
        }

    def run_full(self, input_paths: list[Path]) -> dict:
        """全ステップ実行"""
        # 1. パース + DB投入
        count = self.run_parse_only(input_paths)
        print(f"パース完了: {count}件")

        # 2-6. 未処理エントリを処理
        result = self.run_process_unprocessed()
        result["entries_parsed"] = count
        return result

    def run_parse_only(self, input_paths: list[Path]) -> int:
        """パースしてDB投入のみ（API不使用）"""
        all_entries = []
        for path in input_paths:
            source_type = self._detect_source_type(path)
            if source_type and source_type in self.parsers:
                parser = self.parsers[source_type]
                entries = parser.parse(path)
                all_entries.extend(entries)
                print(f"  {path.name}: {len(entries)}件 ({source_type})")

        inserted = 0
        for entry in all_entries:
            entry_dict = {
                "source_type": entry.source_type,
                "source_id": entry.source_id,
                "title": entry.title,
                "content": entry.content,
                "summary": None,
                "category": None,
                "tags": [],
                "thinking_pattern": None,
                "embedding": None,
                "source_metadata": json.dumps(entry.source_metadata),
                "created_at": entry.created_at,
                "updated_at": entry.updated_at,
            }
            result = self.db.insert_entry(entry_dict)
            if result:
                inserted += 1

        return inserted

    def run_process_unprocessed(self) -> dict:
        """未処理エントリのみ処理"""
        entries = self.db.get_unprocessed_entries()
        if not entries:
            print("未処理エントリなし")
            return {"entries_processed": 0, "sessions_created": 0, "cost": {}}

        print(f"未処理エントリ: {len(entries)}件")

        # RawEntry に変換
        raw_entries = [
            RawEntry(
                source_type=e["source_type"],
                source_id=e["source_id"],
                title=e["title"],
                content=e.get("content", ""),
                created_at=str(e["created_at"]),
                updated_at=str(e["updated_at"]) if e.get("updated_at") else None,
                source_metadata=e.get("source_metadata", {}),
            )
            for e in entries
        ]

        # 2. 分類（バッチ）
        print("カテゴリ分類中...")
        categories = self.categorizer.categorize(raw_entries)

        # 3. 要約 + タグ（1件ずつ）
        print("要約・タグ生成中...")
        summaries = {}
        for i, raw in enumerate(raw_entries):
            result = self.summarizer.summarize(raw)
            if result:
                summaries[raw.source_id] = result
            if i > 0 and i % 5 == 0:
                print(f"  {i}/{len(raw_entries)}件完了")
            time.sleep(self.settings.rate_limit_delay)

        # 4. パターン抽出（Claudeのみ）
        print("思考パターン抽出中...")
        patterns = {}
        for raw in raw_entries:
            if raw.source_type == "claude":
                pattern = self.pattern_extractor.extract(raw)
                if pattern:
                    patterns[raw.source_id] = pattern
                time.sleep(self.settings.rate_limit_delay)

        # 5. embedding生成（バッチ）
        print("embedding生成中...")
        texts_to_embed = []
        entry_ids_for_embed = []
        for e in entries:
            sid = e["source_id"]
            summary_text = summaries.get(sid, {}).get("summary", "")
            if summary_text:
                texts_to_embed.append(summary_text)
                entry_ids_for_embed.append(str(e["id"]))

        embeddings = []
        if texts_to_embed:
            embeddings = self.embedder.embed_batch(texts_to_embed)

        # DBに更新を反映
        print("DB更新中...")
        for e in entries:
            sid = e["source_id"]
            updates = {}

            if sid in categories:
                updates["category"] = categories[sid]
            if sid in summaries:
                updates["summary"] = summaries[sid]["summary"]
                updates["tags"] = summaries[sid]["tags"]
            if sid in patterns:
                updates["thinking_pattern"] = json.dumps(patterns[sid], ensure_ascii=False)

            eid = str(e["id"])
            if eid in entry_ids_for_embed:
                idx = entry_ids_for_embed.index(eid)
                if idx < len(embeddings):
                    updates["embedding"] = embeddings[idx]

            if updates:
                self.db.update_entry(str(e["id"]), updates)

        # 6. セッション統合
        print("セッション統合中...")
        all_entries = self.db.get_unprocessed_entries()
        # 処理済みエントリを取得（summaryがあるもの）
        processed = [
            e for e in self.db.browse_by_period("1900-01-01", "2100-01-01", limit=10000)
            if e.get("embedding") is not None
        ]
        sessions = self.session_builder.build_sessions(processed)
        for session in sessions:
            self.db.insert_session(session)

        cost = self.claude.usage_summary()
        print(f"完了: {len(entries)}件処理, {len(sessions)}セッション生成")
        print(f"コスト: ${cost['estimated_cost_usd']}")

        return {
            "entries_processed": len(entries),
            "sessions_created": len(sessions),
            "cost": cost,
        }

    def _detect_source_type(self, path: Path) -> str | None:
        """ファイルパスからソースタイプを推測"""
        name = path.name.lower()
        if "claude" in name:
            return "claude"
        if "myactivity" in name or "search" in name:
            return "google_search"
        if "browserhistory" in name or "browse" in name:
            return "google_browse"

        # JSONの中身を見て判定
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "Browser History" in data:
                return "google_browse"
            if isinstance(data, list) and data:
                first = data[0]
                if "chat_messages" in first or "uuid" in first:
                    return "claude"
                if "header" in first and first.get("header") == "Search":
                    return "google_search"
        except Exception:
            pass

        return None
