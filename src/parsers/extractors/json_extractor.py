import json
from pathlib import Path

from . import ExtractResult


def _is_conversation(data) -> bool:
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            return "chat_messages" in first or "messages" in first or "uuid" in first
    if isinstance(data, dict):
        return "chat_messages" in data or "uuid" in data
    return False


def extract(file_path: Path) -> ExtractResult:
    text = file_path.read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return ExtractResult(
            title=None,
            content=text[:10000],
            created_at=None,
            extra_metadata={"is_conversation": False, "top_level_type": "invalid", "key_count": 0},
        )

    if _is_conversation(data):
        # claude_parser に委譲するため content="" で返す
        return ExtractResult(
            title=None,
            content="",
            created_at=None,
            extra_metadata={"is_conversation": True, "top_level_type": "list", "key_count": 0},
        )

    top_level_type = "list" if isinstance(data, list) else "object"
    key_count = len(data) if isinstance(data, dict) else len(data) if isinstance(data, list) else 0

    content = json.dumps(data, ensure_ascii=False, indent=2)[:10000]

    return ExtractResult(
        title=None,
        content=content,
        created_at=None,
        extra_metadata={
            "is_conversation": False,
            "top_level_type": top_level_type,
            "key_count": key_count,
        },
    )
