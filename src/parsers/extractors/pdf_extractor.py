from pathlib import Path

from . import ExtractResult


def extract(file_path: Path) -> ExtractResult:
    try:
        import pymupdf
    except ImportError:
        raise ImportError("pymupdf が必要です: uv add pymupdf")

    doc = pymupdf.open(str(file_path))

    pages_text = []
    for page in doc:
        pages_text.append(page.get_text())
    content = "\n".join(pages_text).strip()

    meta = doc.metadata or {}
    pdf_title = meta.get("title") or None
    pdf_author = meta.get("author") or None
    creation_date = meta.get("creationDate") or None

    # タイトル: PDFメタデータ > コンテンツ最初の行
    title = pdf_title
    if not title:
        for line in content.splitlines():
            stripped = line.strip()
            if stripped:
                title = stripped[:100]
                break

    # creationDate: "D:20230101120000+09'00'" 形式 → ISO 8601 に変換
    created_at = None
    if creation_date and creation_date.startswith("D:"):
        raw = creation_date[2:]
        if len(raw) >= 8:
            try:
                created_at = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
                if len(raw) >= 14:
                    created_at += f"T{raw[8:10]}:{raw[10:12]}:{raw[12:14]}"
            except Exception:
                pass

    doc.close()

    return ExtractResult(
        title=title,
        content=content,
        created_at=created_at,
        extra_metadata={
            "page_count": len(pages_text),
            "pdf_title": pdf_title,
            "pdf_author": pdf_author,
        },
    )
