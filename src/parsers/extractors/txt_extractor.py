from pathlib import Path

from . import ExtractResult


def extract(file_path: Path) -> ExtractResult:
    content = None
    encoding_used = "utf-8"

    for enc in ("utf-8", "shift_jis", "cp932"):
        try:
            content = file_path.read_text(encoding=enc)
            encoding_used = enc
            break
        except (UnicodeDecodeError, LookupError):
            continue

    if content is None:
        content = file_path.read_text(encoding="utf-8", errors="replace")

    lines = content.splitlines()
    title = None
    for line in lines:
        stripped = line.strip()
        if stripped:
            title = stripped[:100]
            break

    return ExtractResult(
        title=title,
        content=content.strip(),
        created_at=None,
        extra_metadata={"encoding": encoding_used, "line_count": len(lines)},
    )
