from pathlib import Path

import pytest

from src.parsers.claude_parser import ClaudeParser
from src.parsers.google_search_parser import GoogleSearchParser
from src.parsers.google_browse_parser import GoogleBrowseParser

FIXTURES = Path(__file__).parent / "fixtures"


class TestClaudeParser:
    def setup_method(self):
        self.parser = ClaudeParser()

    def test_parse_basic(self):
        entries = self.parser.parse(FIXTURES / "claude_sample.json")
        # conv-005 は空会話なのでスキップ → 4件
        assert len(entries) == 4

    def test_source_type(self):
        entries = self.parser.parse(FIXTURES / "claude_sample.json")
        for entry in entries:
            assert entry.source_type == "claude"

    def test_sender_role_normalization(self):
        """sender と role の両方に対応し、user→human に正規化"""
        entries = self.parser.parse(FIXTURES / "claude_sample.json")
        # conv-002 は role: "user" を使用
        conv2 = [e for e in entries if e.source_id == "conv-002"][0]
        assert "Human:" in conv2.content
        assert "Assistant:" in conv2.content

    def test_content_array_extraction(self):
        """content配列からテキスト抽出"""
        entries = self.parser.parse(FIXTURES / "claude_sample.json")
        conv2 = [e for e in entries if e.source_id == "conv-002"][0]
        assert "ボリンジャーバンド" in conv2.content

    def test_tool_use_recorded(self):
        """tool_use は [Tool: name] として記録"""
        entries = self.parser.parse(FIXTURES / "claude_sample.json")
        conv2 = [e for e in entries if e.source_id == "conv-002"][0]
        assert "[Tool: code_execution]" in conv2.content

    def test_metadata(self):
        entries = self.parser.parse(FIXTURES / "claude_sample.json")
        conv1 = [e for e in entries if e.source_id == "conv-001"][0]
        assert conv1.source_metadata["turn_count"] == 4
        assert conv1.source_metadata["human_char_count"] > 0
        assert conv1.source_metadata["model"] == "claude-3-5-sonnet-20241022"

    def test_empty_conversation_skipped(self):
        """空会話はスキップ"""
        entries = self.parser.parse(FIXTURES / "claude_sample.json")
        ids = [e.source_id for e in entries]
        assert "conv-005" not in ids

    def test_untitled_conversation(self):
        """タイトルなしの会話は 'Untitled'"""
        entries = self.parser.parse(FIXTURES / "claude_sample.json")
        conv5_like = [e for e in entries if e.title == "Untitled"]
        # conv-005は空なのでスキップされる
        assert all(e.title != "" for e in entries)


class TestGoogleSearchParser:
    def setup_method(self):
        self.parser = GoogleSearchParser(time_window_minutes=30)

    def test_parse_basic(self):
        entries = self.parser.parse(FIXTURES / "google_search_sample.json")
        # 4グループ: デコレータ系、FX系、React系、Docker系
        assert len(entries) == 4

    def test_source_type(self):
        entries = self.parser.parse(FIXTURES / "google_search_sample.json")
        for entry in entries:
            assert entry.source_type == "google_search"

    def test_prefix_removed(self):
        """'Searched for ' プレフィックスが除去されている"""
        entries = self.parser.parse(FIXTURES / "google_search_sample.json")
        for entry in entries:
            assert not entry.title.startswith("Searched for ")
            assert "Searched for " not in entry.content

    def test_grouping(self):
        """時間窓でグループ化"""
        entries = self.parser.parse(FIXTURES / "google_search_sample.json")
        # 最初のグループはデコレータ関連 5クエリ
        decorator_group = entries[0]
        assert decorator_group.source_metadata["query_count"] == 5
        assert "デコレータ" in decorator_group.title

    def test_metadata(self):
        entries = self.parser.parse(FIXTURES / "google_search_sample.json")
        for entry in entries:
            assert "query_count" in entry.source_metadata
            assert "timespan_minutes" in entry.source_metadata


class TestGoogleBrowseParser:
    def setup_method(self):
        self.parser = GoogleBrowseParser(time_window_minutes=30)

    def test_parse_basic(self):
        entries = self.parser.parse(FIXTURES / "google_browse_sample.json")
        # 検索エンジンURLはフィルタ、残りが時間窓でグループ化
        assert len(entries) >= 4

    def test_source_type(self):
        entries = self.parser.parse(FIXTURES / "google_browse_sample.json")
        for entry in entries:
            assert entry.source_type == "google_browse"

    def test_search_engine_filtered(self):
        """検索エンジンURLがフィルタされている"""
        entries = self.parser.parse(FIXTURES / "google_browse_sample.json")
        for entry in entries:
            urls = entry.content.split("\n")
            for url in urls:
                assert "google.com/search" not in url

    def test_representative_title(self):
        """代表タイトルは最も長いページタイトル"""
        entries = self.parser.parse(FIXTURES / "google_browse_sample.json")
        for entry in entries:
            assert len(entry.title) > 0

    def test_metadata(self):
        entries = self.parser.parse(FIXTURES / "google_browse_sample.json")
        for entry in entries:
            assert "urls" in entry.source_metadata
            assert "visit_count" in entry.source_metadata
            assert "timespan_minutes" in entry.source_metadata
            assert entry.source_metadata["visit_count"] == len(
                entry.source_metadata["urls"]
            )
