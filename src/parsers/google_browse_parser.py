import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .base import BaseParser, RawEntry

DEFAULT_TIME_WINDOW = 30

# フィルタ対象の検索エンジンドメイン
SEARCH_ENGINE_DOMAINS = {
    "www.google.com",
    "google.com",
    "www.bing.com",
    "bing.com",
    "search.yahoo.com",
    "duckduckgo.com",
    "www.duckduckgo.com",
}


class GoogleBrowseParser(BaseParser):
    """Googleブラウザ履歴（Google Takeout）パーサー"""

    def __init__(self, time_window_minutes: int = DEFAULT_TIME_WINDOW):
        self.time_window_minutes = time_window_minutes

    def parse(self, input_path: str | Path) -> list[RawEntry]:
        input_path = Path(input_path)
        data = json.loads(input_path.read_text(encoding="utf-8"))

        # BrowserHistory.json は {"Browser History": [...]} の形式
        history = data.get("Browser History", data) if isinstance(data, dict) else data

        pages = self._extract_pages(history)
        if not pages:
            return []

        pages.sort(key=lambda x: x["time"])
        groups = self._group_by_time_window(pages)

        return [self._group_to_entry(group) for group in groups]

    def _extract_pages(self, history: list[dict]) -> list[dict]:
        pages = []
        for item in history:
            url = item.get("url", "")
            if self._is_search_engine(url):
                continue

            title = item.get("title", "")
            time_usec = item.get("time_usec", 0)

            if not time_usec:
                continue

            dt = datetime.fromtimestamp(time_usec / 1_000_000, tz=timezone.utc)
            pages.append({"url": url, "title": title, "time": dt})
        return pages

    def _is_search_engine(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            domain = parsed.hostname or ""
            if domain in SEARCH_ENGINE_DOMAINS and "/search" in parsed.path:
                return True
        except Exception:
            pass
        return False

    def _group_by_time_window(self, pages: list[dict]) -> list[list[dict]]:
        if not pages:
            return []

        groups = [[pages[0]]]
        for p in pages[1:]:
            last = groups[-1][-1]
            diff = (p["time"] - last["time"]).total_seconds() / 60
            if diff <= self.time_window_minutes:
                groups[-1].append(p)
            else:
                groups.append([p])
        return groups

    def _group_to_entry(self, group: list[dict]) -> RawEntry:
        urls = [p["url"] for p in group]
        titles = [p["title"] for p in group if p["title"]]
        first_time = group[0]["time"]
        last_time = group[-1]["time"]
        timespan = (last_time - first_time).total_seconds() / 60

        # 代表タイトル: 最も長いページタイトル
        representative_title = max(titles, key=len) if titles else urls[0]

        id_source = f"{first_time.isoformat()}_{urls[0]}"
        source_id = hashlib.sha256(id_source.encode()).hexdigest()[:16]

        return RawEntry(
            source_type="google_browse",
            source_id=source_id,
            title=representative_title,
            content="\n".join(urls),
            created_at=first_time.isoformat(),
            updated_at=last_time.isoformat() if len(group) > 1 else None,
            source_metadata={
                "urls": urls,
                "visit_count": len(urls),
                "timespan_minutes": round(timespan, 1),
            },
        )
