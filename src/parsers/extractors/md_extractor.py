import re
from pathlib import Path

from . import ExtractResult


def extract(file_path: Path) -> ExtractResult:
    text = file_path.read_text(encoding="utf-8", errors="replace")

    title = None
    created_at = None
    has_frontmatter = False

    # YAML frontmatter
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            has_frontmatter = True
            fm_block = text[3:end]
            # title
            m = re.search(r"^title:\s*(.+)$", fm_block, re.MULTILINE)
            if m:
                title = m.group(1).strip().strip('"\'')
            # date
            m = re.search(r"^date:\s*(.+)$", fm_block, re.MULTILINE)
            if m:
                created_at = m.group(1).strip().strip('"\'')
            # content starts after closing ---
            text = text[end + 4:]

    # 最初の # 見出し
    if title is None:
        m = re.search(r"^#{1,6}\s+(.+)$", text, re.MULTILINE)
        if m:
            title = m.group(1).strip()

    heading_count = len(re.findall(r"^#{1,6}\s", text, re.MULTILINE))

    return ExtractResult(
        title=title,
        content=text.strip(),
        created_at=created_at,
        extra_metadata={"has_frontmatter": has_frontmatter, "heading_count": heading_count},
    )
