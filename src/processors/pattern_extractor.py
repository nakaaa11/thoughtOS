from pathlib import Path

from ..claude_client import ClaudeClient
from ..parsers.base import RawEntry

PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"


class PatternExtractor:
    def __init__(self, claude_client: ClaudeClient, min_turns: int = 3):
        self.claude = claude_client
        self.min_turns = min_turns

    def extract(self, entry: RawEntry) -> dict | None:
        """思考パターン抽出。Claude会話のみ対象。"""
        if entry.source_type != "claude":
            return None

        turn_count = entry.source_metadata.get("turn_count", 0)
        if turn_count < self.min_turns:
            return None

        content = entry.content or ""
        # 長い会話は要約してから分析
        if len(content) > 10000:
            summary_prompt = (
                f"以下の会話を3000文字以内に要約してください。"
                f"ユーザーの質問パターンと思考過程を保持してください。\n\n{content}"
            )
            content = self.claude.query(summary_prompt)

        prompt = self.claude.load_prompt(
            PROMPTS_DIR / "extract_pattern.txt",
            title=entry.title,
            created_at=entry.created_at,
            content=content,
        )

        return self.claude.query_json(prompt)
