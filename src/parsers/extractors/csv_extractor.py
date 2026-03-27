import csv
import io
from pathlib import Path

from . import ExtractResult


def extract(file_path: Path) -> ExtractResult:
    content_raw = None
    encoding_used = "utf-8"

    for enc in ("utf-8", "utf-8-sig", "shift_jis", "cp932"):
        try:
            content_raw = file_path.read_text(encoding=enc)
            encoding_used = enc
            break
        except (UnicodeDecodeError, LookupError):
            continue

    if content_raw is None:
        content_raw = file_path.read_text(encoding="utf-8", errors="replace")

    reader = csv.DictReader(io.StringIO(content_raw))
    columns = reader.fieldnames or []

    lines = []
    row_count = 0
    for row in reader:
        parts = [f"{k}: {v}" for k, v in row.items() if v is not None]
        lines.append(" | ".join(parts))
        row_count += 1

    content = "\n".join(lines)

    return ExtractResult(
        title=None,
        content=content,
        created_at=None,
        extra_metadata={
            "column_count": len(columns),
            "row_count": row_count,
            "columns": list(columns),
            "encoding": encoding_used,
        },
    )
