from pathlib import Path

from ..claude_client import ClaudeClient
from ..parsers.base import RawEntry

PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"


class Summarizer:
    def __init__(self, claude_client: ClaudeClient):
        self.claude = claude_client

    def summarize(self, entry: RawEntry) -> dict | None:
        """要約+タグ生成。戻り値: {"summary": str, "tags": list[str]}"""
        content = entry.content or ""
        # 長すぎる場合は先頭10000文字に制限
        if len(content) > 10000:
            content = content[:10000] + "\n...(以下省略)"

        prompt = self.claude.load_prompt(
            PROMPTS_DIR / "summarize.txt",
            title=entry.title,
            source_type=entry.source_type,
            content=content,
        )

        result = self.claude.query_json(prompt)
        if not result or not isinstance(result, dict):
            return None

        return {
            "summary": result.get("summary", ""),
            "tags": result.get("tags", []),
        }
