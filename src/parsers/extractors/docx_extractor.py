from pathlib import Path

from . import ExtractResult


def extract(file_path: Path) -> ExtractResult:
    try:
        from docx import Document
    except ImportError:
        raise ImportError("python-docx が必要です: uv add python-docx")

    doc = Document(str(file_path))

    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    content = "\n".join(paragraphs)

    props = doc.core_properties
    docx_title = props.title or None
    docx_author = props.author or None
    created = props.created

    title = docx_title
    if not title and paragraphs:
        title = paragraphs[0][:100]

    created_at = created.isoformat() if created else None

    return ExtractResult(
        title=title,
        content=content,
        created_at=created_at,
        extra_metadata={
            "paragraph_count": len(paragraphs),
            "docx_author": docx_author,
        },
    )
