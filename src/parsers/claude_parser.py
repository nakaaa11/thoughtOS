import json
import zipfile
from pathlib import Path

from .base import BaseParser, RawEntry


class ClaudeParser(BaseParser):
    """Claude会話エクスポートJSON パーサー"""

    def parse(self, input_path: str | Path) -> list[RawEntry]:
        input_path = Path(input_path)

        if input_path.is_dir():
            conversations = []
            for f in sorted(input_path.glob("*.json")):
                conversations.extend(self._load_json(f))
            return self._parse_conversations(conversations)

        if input_path.suffix == ".zip":
            return self._parse_zip(input_path)

        conversations = self._load_json(input_path)
        return self._parse_conversations(conversations)

    def _load_json(self, path: Path) -> list[dict]:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        return [data]

    def _parse_zip(self, zip_path: Path) -> list[RawEntry]:
        conversations = []
        with zipfile.ZipFile(zip_path) as zf:
            for name in zf.namelist():
                if name.endswith(".json"):
                    data = json.loads(zf.read(name).decode("utf-8"))
                    if isinstance(data, list):
                        conversations.extend(data)
                    else:
                        conversations.append(data)
        return self._parse_conversations(conversations)

    def _parse_conversations(self, conversations: list[dict]) -> list[RawEntry]:
        entries = []
        for conv in conversations:
            entry = self._parse_single(conv)
            if entry:
                entries.append(entry)
        return entries

    def _parse_single(self, conv: dict) -> RawEntry | None:
        messages = conv.get("chat_messages", [])
        if not messages:
            return None

        lines = []
        human_chars = 0
        assistant_chars = 0
        turn_count = 0

        for msg in messages:
            role = self._normalize_role(msg)
            text = self._extract_text(msg)
            if not text:
                continue

            label = "Human" if role == "human" else "Assistant"
            lines.append(f"{label}: {text}")
            turn_count += 1

            if role == "human":
                human_chars += len(text)
            else:
                assistant_chars += len(text)

        if not lines:
            return None

        content = "\n".join(lines)
        title = conv.get("name", "") or conv.get("title", "") or "Untitled"

        return RawEntry(
            source_type="claude",
            source_id=conv.get("uuid", ""),
            title=title,
            content=content,
            created_at=conv.get("created_at", ""),
            updated_at=conv.get("updated_at"),
            source_metadata={
                "turn_count": turn_count,
                "human_char_count": human_chars,
                "assistant_char_count": assistant_chars,
                "model": conv.get("model", ""),
            },
        )

    def _normalize_role(self, msg: dict) -> str:
        role = msg.get("sender") or msg.get("role", "")
        if role in ("user", "human"):
            return "human"
        return "assistant"

    def _extract_text(self, msg: dict) -> str:
        # text フィールドを優先
        if msg.get("text"):
            return msg["text"]

        # content 配列からテキスト抽出
        content = msg.get("content", [])
        if isinstance(content, str):
            return content

        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif item.get("type") == "tool_use":
                    parts.append(f"[Tool: {item.get('name', 'unknown')}]")
        return "\n".join(parts)
