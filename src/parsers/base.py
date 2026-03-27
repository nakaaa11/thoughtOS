from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RawEntry:
    """パーサーの共通出力フォーマット"""

    source_type: str
    source_id: str
    title: str
    content: str
    created_at: str  # ISO 8601
    updated_at: str | None
    source_metadata: dict[str, Any] = field(default_factory=dict)
    file_hash: str | None = None  # ファイルインポート時のみ設定（重複検出用）


class BaseParser:
    """パーサー基底クラス"""

    def parse(self, input_path: str | Path) -> list[RawEntry]:
        raise NotImplementedError
