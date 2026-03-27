"""ファイルインポート用パーサー"""
import hashlib
from datetime import datetime
from pathlib import Path

from .base import BaseParser, RawEntry
from .extractors import md_extractor, txt_extractor, pdf_extractor
from .extractors import docx_extractor, csv_extractor, json_extractor

EXTRACTOR_MAP = {
    ".md": ("file_md", md_extractor.extract),
    ".markdown": ("file_md", md_extractor.extract),
    ".txt": ("file_txt", txt_extractor.extract),
    ".text": ("file_txt", txt_extractor.extract),
    ".pdf": ("file_pdf", pdf_extractor.extract),
    ".docx": ("file_docx", docx_extractor.extract),
    ".csv": ("file_csv", csv_extractor.extract),
    ".json": ("file_json", json_extractor.extract),
}


class FileParser(BaseParser):
    def parse(self, input_path: str | Path) -> list[RawEntry]:
        path = Path(input_path)
        if path.is_dir():
            return self._parse_directory(path)
        return self._parse_file(path)

    def _parse_directory(self, dir_path: Path) -> list[RawEntry]:
        entries = []
        seen_exts = set(EXTRACTOR_MAP.keys())
        for file_path in sorted(dir_path.rglob("*")):
            if file_path.suffix.lower() in seen_exts:
                entries.extend(self._parse_file(file_path))
        return entries

    def _parse_file(self, file_path: Path) -> list[RawEntry]:
        ext = file_path.suffix.lower()
        if ext not in EXTRACTOR_MAP:
            print(f"  ⚠ 未対応の拡張子: {file_path.name}")
            return []

        source_type, extractor_fn = EXTRACTOR_MAP[ext]
        file_hash = self._compute_hash(file_path)

        try:
            result = extractor_fn(file_path)
        except Exception as e:
            print(f"  ✗ 抽出エラー ({file_path.name}): {e}")
            return []

        # JSON が会話形式と判定された場合は claude_parser に委譲
        if source_type == "file_json" and result.extra_metadata.get("is_conversation"):
            from .claude_parser import ClaudeParser
            return ClaudeParser().parse(file_path)

        if not result.content.strip():
            print(f"  ⚠ コンテンツ空 (スキップ): {file_path.name}")
            return []

        entry = RawEntry(
            source_type=source_type,
            source_id=f"file:{file_hash[:16]}",
            title=result.title or file_path.stem,
            content=result.content,
            created_at=result.created_at or self._get_file_mtime(file_path),
            updated_at=None,
            source_metadata={
                "file_name": file_path.name,
                "file_size_bytes": file_path.stat().st_size,
                "file_path": str(file_path.absolute()),
                "char_count": len(result.content),
                **result.extra_metadata,
            },
            file_hash=file_hash,
        )
        return [entry]

    @staticmethod
    def _compute_hash(file_path: Path) -> str:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _get_file_mtime(file_path: Path) -> str:
        mtime = file_path.stat().st_mtime
        return datetime.fromtimestamp(mtime).isoformat()
