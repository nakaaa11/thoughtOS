from pathlib import Path

from ..claude_client import ClaudeClient
from ..parsers.base import RawEntry

PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"


class Categorizer:
    def __init__(self, claude_client: ClaudeClient, batch_size: int = 10):
        self.claude = claude_client
        self.batch_size = batch_size

    def categorize(self, entries: list[RawEntry]) -> dict[str, str]:
        """エントリをカテゴリ分類。戻り値: {source_id: category}"""
        results = {}
        for i in range(0, len(entries), self.batch_size):
            batch = entries[i : i + self.batch_size]
            batch_results = self._categorize_batch(batch)
            results.update(batch_results)
        return results

    def _categorize_batch(self, batch: list[RawEntry]) -> dict[str, str]:
        entries_text = "\n".join(
            f"- source_id: {e.source_id} | title: {e.title} | "
            f"content (先頭200文字): {(e.content or '')[:200]}"
            for e in batch
        )

        prompt = self.claude.load_prompt(
            PROMPTS_DIR / "categorize.txt", entries=entries_text
        )

        result = self.claude.query_json(prompt)
        if not result or not isinstance(result, list):
            return {}

        return {item["source_id"]: item["category"] for item in result if "source_id" in item}
