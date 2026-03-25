"""プロセッサーのユニットテスト（外部API不使用）"""

import math
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.parsers.base import RawEntry
from src.processors.categorizer import Categorizer
from src.processors.pattern_extractor import PatternExtractor
from src.processors.session_builder import SessionBuilder
from src.processors.summarizer import Summarizer


def make_entry(
    source_type="claude",
    source_id="test-001",
    title="テストタイトル",
    content="テストコンテンツ",
    turn_count=5,
) -> RawEntry:
    return RawEntry(
        source_type=source_type,
        source_id=source_id,
        title=title,
        content=content,
        created_at="2025-01-01T00:00:00Z",
        updated_at=None,
        source_metadata={"turn_count": turn_count},
    )


# ============================================================
# Categorizer
# ============================================================


class TestCategorizer:
    def setup_method(self):
        self.mock_claude = MagicMock()
        self.categorizer = Categorizer(self.mock_claude, batch_size=3)

    def test_returns_empty_on_invalid_api_response(self):
        self.mock_claude.query_json.return_value = None
        self.mock_claude.load_prompt.return_value = "prompt"
        entries = [make_entry(source_id=f"id-{i}") for i in range(2)]
        result = self.categorizer.categorize(entries)
        assert result == {}

    def test_returns_empty_on_non_list_response(self):
        self.mock_claude.query_json.return_value = {"error": "bad"}
        self.mock_claude.load_prompt.return_value = "prompt"
        entries = [make_entry(source_id="id-1")]
        result = self.categorizer.categorize(entries)
        assert result == {}

    def test_categorize_maps_source_id_to_category(self):
        self.mock_claude.load_prompt.return_value = "prompt"
        self.mock_claude.query_json.return_value = [
            {"source_id": "id-1", "category": "技術学習"},
            {"source_id": "id-2", "category": "アプリ開発"},
        ]
        entries = [
            make_entry(source_id="id-1"),
            make_entry(source_id="id-2"),
        ]
        result = self.categorizer.categorize(entries)
        assert result == {"id-1": "技術学習", "id-2": "アプリ開発"}

    def test_batch_splits_correctly(self):
        """batch_size=3 で 7件 → 3回APIコール"""
        self.mock_claude.load_prompt.return_value = "prompt"
        self.mock_claude.query_json.return_value = []
        entries = [make_entry(source_id=f"id-{i}") for i in range(7)]
        self.categorizer.categorize(entries)
        assert self.mock_claude.query_json.call_count == 3

    def test_ignores_items_without_source_id(self):
        self.mock_claude.load_prompt.return_value = "prompt"
        self.mock_claude.query_json.return_value = [
            {"category": "技術学習"},  # source_id なし → 無視
            {"source_id": "id-1", "category": "調査・リサーチ"},
        ]
        entries = [make_entry(source_id="id-1")]
        result = self.categorizer.categorize(entries)
        assert result == {"id-1": "調査・リサーチ"}


# ============================================================
# Summarizer
# ============================================================


class TestSummarizer:
    def setup_method(self):
        self.mock_claude = MagicMock()
        self.summarizer = Summarizer(self.mock_claude)

    def test_returns_none_on_invalid_response(self):
        self.mock_claude.query_json.return_value = None
        self.mock_claude.load_prompt.return_value = "prompt"
        result = self.summarizer.summarize(make_entry())
        assert result is None

    def test_returns_none_on_non_dict_response(self):
        self.mock_claude.query_json.return_value = ["not", "a", "dict"]
        self.mock_claude.load_prompt.return_value = "prompt"
        result = self.summarizer.summarize(make_entry())
        assert result is None

    def test_returns_summary_and_tags(self):
        self.mock_claude.load_prompt.return_value = "prompt"
        self.mock_claude.query_json.return_value = {
            "summary": "テスト要約",
            "tags": ["Python", "AI"],
        }
        result = self.summarizer.summarize(make_entry())
        assert result == {"summary": "テスト要約", "tags": ["Python", "AI"]}

    def test_truncates_long_content(self):
        """10001文字のコンテンツは先頭10000文字+省略サフィックスに切り詰められる"""
        self.mock_claude.load_prompt.return_value = "prompt"
        self.mock_claude.query_json.return_value = {"summary": "s", "tags": []}
        long_entry = make_entry(content="x" * 10001)
        self.summarizer.summarize(long_entry)
        call_kwargs = self.mock_claude.load_prompt.call_args
        content_arg = call_kwargs[1]["content"]
        assert content_arg.startswith("x" * 10000)
        assert "省略" in content_arg

    def test_exact_10000_chars_not_truncated(self):
        """ちょうど10000文字は切り詰めない"""
        self.mock_claude.load_prompt.return_value = "prompt"
        self.mock_claude.query_json.return_value = {"summary": "s", "tags": []}
        entry = make_entry(content="x" * 10000)
        self.summarizer.summarize(entry)
        call_kwargs = self.mock_claude.load_prompt.call_args
        content_arg = call_kwargs[1]["content"]
        assert content_arg == "x" * 10000

    def test_empty_dict_response_returns_none(self):
        """APIが空dictを返した場合（falsyなので）Noneが返る"""
        self.mock_claude.load_prompt.return_value = "prompt"
        self.mock_claude.query_json.return_value = {}
        result = self.summarizer.summarize(make_entry())
        assert result is None

    def test_partial_keys_return_defaults(self):
        """summaryだけ返ってtagsがない場合も空リストで補完される"""
        self.mock_claude.load_prompt.return_value = "prompt"
        self.mock_claude.query_json.return_value = {"summary": "要約のみ"}
        result = self.summarizer.summarize(make_entry())
        assert result == {"summary": "要約のみ", "tags": []}


# ============================================================
# PatternExtractor
# ============================================================


class TestPatternExtractor:
    def setup_method(self):
        self.mock_claude = MagicMock()
        self.extractor = PatternExtractor(self.mock_claude, min_turns=3)

    def test_non_claude_source_returns_none(self):
        entry = make_entry(source_type="google_search")
        result = self.extractor.extract(entry)
        assert result is None

    def test_too_few_turns_returns_none(self):
        entry = make_entry(source_type="claude", turn_count=2)
        result = self.extractor.extract(entry)
        assert result is None

    def test_exactly_min_turns_is_processed(self):
        self.mock_claude.load_prompt.return_value = "prompt"
        self.mock_claude.query_json.return_value = {"question_style": "直接的"}
        entry = make_entry(source_type="claude", turn_count=3)
        result = self.extractor.extract(entry)
        assert result == {"question_style": "直接的"}

    def test_long_content_gets_pre_summarized(self):
        """10001文字以上の会話は先にsummaryしてからextract"""
        self.mock_claude.query.return_value = "要約済み会話"
        self.mock_claude.load_prompt.return_value = "prompt"
        self.mock_claude.query_json.return_value = {}
        entry = make_entry(source_type="claude", turn_count=5, content="x" * 10001)
        self.extractor.extract(entry)
        # query (要約) が呼ばれたことを確認
        assert self.mock_claude.query.called


# ============================================================
# SessionBuilder（純粋ロジック — APIモック不要）
# ============================================================


def make_db_entry(entry_id, created_at, embedding, source_type="claude", summary="s"):
    return {
        "id": entry_id,
        "created_at": created_at,
        "embedding": embedding,
        "source_type": source_type,
        "title": f"タイトル_{entry_id}",
        "summary": summary,
    }


class TestSessionBuilderPureLogic:
    def setup_method(self):
        self.builder = SessionBuilder(
            claude_client=MagicMock(),
            embedder=MagicMock(),
            time_window_minutes=30,
            similarity_threshold=0.7,
        )

    # ---- cosine_similarity ----

    def test_cosine_similarity_identical_vectors(self):
        v = [1.0, 0.0, 0.0]
        assert self.builder._cosine_similarity(v, v) == pytest.approx(1.0)

    def test_cosine_similarity_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert self.builder._cosine_similarity(a, b) == pytest.approx(0.0)

    def test_cosine_similarity_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert self.builder._cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_cosine_similarity_zero_vector(self):
        a = [0.0, 0.0]
        b = [1.0, 0.0]
        assert self.builder._cosine_similarity(a, b) == 0.0

    # ---- _group_by_time_window ----

    def test_group_by_time_window_empty(self):
        assert self.builder._group_by_time_window([]) == []

    def test_group_by_time_window_single(self):
        base = datetime(2025, 1, 1, 10, 0)
        e = make_db_entry("a", base, [1.0, 0.0])
        groups = self.builder._group_by_time_window([e])
        assert len(groups) == 1
        assert len(groups[0]) == 1

    def test_group_by_time_window_within_window(self):
        base = datetime(2025, 1, 1, 10, 0)
        e1 = make_db_entry("a", base, [1.0, 0.0])
        e2 = make_db_entry("b", base + timedelta(minutes=20), [1.0, 0.0])
        groups = self.builder._group_by_time_window([e1, e2])
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_group_by_time_window_across_window(self):
        base = datetime(2025, 1, 1, 10, 0)
        e1 = make_db_entry("a", base, [1.0, 0.0])
        e2 = make_db_entry("b", base + timedelta(minutes=31), [1.0, 0.0])
        groups = self.builder._group_by_time_window([e1, e2])
        assert len(groups) == 2

    def test_group_by_time_window_exactly_at_boundary(self):
        """ちょうど30分 → 同グループ"""
        base = datetime(2025, 1, 1, 10, 0)
        e1 = make_db_entry("a", base, [1.0, 0.0])
        e2 = make_db_entry("b", base + timedelta(minutes=30), [1.0, 0.0])
        groups = self.builder._group_by_time_window([e1, e2])
        assert len(groups) == 1

    # ---- _average_embedding ----

    def test_average_embedding_single(self):
        embs = [[1.0, 2.0, 3.0]]
        result = self.builder._average_embedding(embs)
        assert result == pytest.approx([1.0, 2.0, 3.0])

    def test_average_embedding_two(self):
        embs = [[1.0, 0.0], [0.0, 1.0]]
        result = self.builder._average_embedding(embs)
        assert result == pytest.approx([0.5, 0.5])

    def test_average_embedding_empty(self):
        assert self.builder._average_embedding([]) == []

    # ---- build_sessions ----

    def test_build_sessions_empty_input(self):
        result = self.builder.build_sessions([])
        assert result == []

    def test_build_sessions_no_embeddings(self):
        base = datetime(2025, 1, 1, 10, 0)
        entries = [
            {"id": "a", "created_at": base, "embedding": None},
            {"id": "b", "created_at": base + timedelta(minutes=5), "embedding": None},
        ]
        result = self.builder.build_sessions(entries)
        assert result == []

    def test_build_sessions_single_entry_no_session(self):
        """1件しかないグループはセッション化しない"""
        base = datetime(2025, 1, 1, 10, 0)
        e = make_db_entry("a", base, [1.0, 0.0])
        result = self.builder.build_sessions([e])
        assert result == []
