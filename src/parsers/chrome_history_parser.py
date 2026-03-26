"""Chrome ローカル履歴DB（SQLite）から直接取得するパーサー"""

import hashlib
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from .base import BaseParser, RawEntry

DEFAULT_TIME_WINDOW = 30

# Chrome の時刻基点: 1601-01-01 00:00:00 UTC
CHROME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)

# 無視するドメイン（検索エンジン・SNS・メール等）
IGNORE_DOMAINS = {
    "www.google.com", "google.com",
    "www.bing.com", "bing.com",
    "search.yahoo.com", "duckduckgo.com",
    "mail.google.com", "accounts.google.com",
}

# Chrome 履歴DBのデフォルトパス（OS別）
CHROME_HISTORY_PATHS = [
    # Mac
    Path.home() / "Library/Application Support/Google/Chrome/Default/History",
    # Linux
    Path.home() / ".config/google-chrome/Default/History",
    # Windows
    Path.home() / "AppData/Local/Google/Chrome/User Data/Default/History",
]


def find_chrome_history_db() -> Path | None:
    for p in CHROME_HISTORY_PATHS:
        if p.exists():
            return p
    return None


def chrome_time_to_datetime(chrome_usec: int) -> datetime:
    return CHROME_EPOCH + timedelta(microseconds=chrome_usec)


class ChromeHistoryParser(BaseParser):
    """Chrome ローカル履歴DBから当日分（または指定日）の履歴を取得"""

    def __init__(
        self,
        time_window_minutes: int = DEFAULT_TIME_WINDOW,
        db_path: Path | None = None,
    ):
        self.time_window_minutes = time_window_minutes
        self.db_path = db_path

    def parse(self, input_path: str | Path | None = None) -> list[RawEntry]:
        """
        input_path: Chromeの履歴DBパス（省略時は自動検出）
        """
        path = Path(input_path) if input_path else self.db_path or find_chrome_history_db()
        if path is None or not path.exists():
            raise FileNotFoundError(
                "Chrome 履歴DBが見つかりません。"
                " --db-path で明示的に指定してください。\n"
                f"  検索場所: {[str(p) for p in CHROME_HISTORY_PATHS]}"
            )
        return self._parse_db(path)

    def parse_today(self, db_path: Path | None = None) -> list[RawEntry]:
        """本日分のみ取得"""
        now = datetime.now(tz=timezone.utc)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return self.parse_range(start, now, db_path)

    def parse_range(
        self,
        start: datetime,
        end: datetime,
        db_path: Path | None = None,
    ) -> list[RawEntry]:
        path = db_path or self.db_path or find_chrome_history_db()
        if path is None or not path.exists():
            raise FileNotFoundError("Chrome 履歴DBが見つかりません")

        start_usec = int((start - CHROME_EPOCH).total_seconds() * 1_000_000)
        end_usec = int((end - CHROME_EPOCH).total_seconds() * 1_000_000)
        return self._parse_db(path, start_usec=start_usec, end_usec=end_usec)

    def _parse_db(
        self,
        db_path: Path,
        start_usec: int | None = None,
        end_usec: int | None = None,
    ) -> list[RawEntry]:
        # Chrome が開いていると DB がロックされるため一時コピーを使う
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        shutil.copy2(db_path, tmp_path)

        try:
            browse_entries, search_entries = self._query_db(
                tmp_path, start_usec, end_usec
            )
        finally:
            tmp_path.unlink(missing_ok=True)

        results = []
        results.extend(self._build_browse_entries(browse_entries))
        results.extend(self._build_search_entries(search_entries))
        return results

    def _query_db(
        self,
        db_path: Path,
        start_usec: int | None,
        end_usec: int | None,
    ) -> tuple[list[dict], list[dict]]:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        time_filter = ""
        params: list = []
        if start_usec is not None:
            time_filter += " AND v.visit_time >= ?"
            params.append(start_usec)
        if end_usec is not None:
            time_filter += " AND v.visit_time <= ?"
            params.append(end_usec)

        sql = f"""
            SELECT u.url, u.title, v.visit_time
            FROM visits v
            JOIN urls u ON v.url = u.id
            WHERE u.hidden = 0
            {time_filter}
            ORDER BY v.visit_time ASC
        """

        rows = conn.execute(sql, params).fetchall()
        conn.close()

        browse_rows = []
        search_rows = []

        for row in rows:
            url = row["url"]
            title = row["title"] or ""
            dt = chrome_time_to_datetime(row["visit_time"])

            query = self._extract_search_query(url)
            if query:
                search_rows.append({"query": query, "time": dt})
            elif not self._is_ignored(url):
                browse_rows.append({"url": url, "title": title, "time": dt})

        return browse_rows, search_rows

    def _extract_search_query(self, url: str) -> str | None:
        """Google/Bing等の検索URLからクエリ文字列を抽出"""
        try:
            parsed = urlparse(url)
            domain = parsed.hostname or ""
            if domain in ("www.google.com", "google.com") and "/search" in parsed.path:
                q = parse_qs(parsed.query).get("q", [])
                return q[0] if q else None
            if domain in ("www.bing.com", "bing.com") and "/search" in parsed.path:
                q = parse_qs(parsed.query).get("q", [])
                return q[0] if q else None
        except Exception:
            pass
        return None

    def _is_ignored(self, url: str) -> bool:
        try:
            domain = urlparse(url).hostname or ""
            return domain in IGNORE_DOMAINS
        except Exception:
            return False

    def _group_by_time_window(self, items: list[dict]) -> list[list[dict]]:
        if not items:
            return []
        groups = [[items[0]]]
        for item in items[1:]:
            diff = (item["time"] - groups[-1][-1]["time"]).total_seconds() / 60
            if diff <= self.time_window_minutes:
                groups[-1].append(item)
            else:
                groups.append([item])
        return groups

    def _build_browse_entries(self, pages: list[dict]) -> list[RawEntry]:
        groups = self._group_by_time_window(pages)
        entries = []
        for group in groups:
            urls = [p["url"] for p in group]
            titles = [p["title"] for p in group if p["title"]]
            first_time = group[0]["time"]
            last_time = group[-1]["time"]
            timespan = (last_time - first_time).total_seconds() / 60
            representative_title = max(titles, key=len) if titles else urls[0]

            source_id = hashlib.sha256(
                f"{first_time.isoformat()}_{urls[0]}".encode()
            ).hexdigest()[:16]

            entries.append(RawEntry(
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
            ))
        return entries

    def _build_search_entries(self, searches: list[dict]) -> list[RawEntry]:
        groups = self._group_by_time_window(searches)
        entries = []
        for group in groups:
            queries = [s["query"] for s in group]
            first_time = group[0]["time"]
            last_time = group[-1]["time"]
            timespan = (last_time - first_time).total_seconds() / 60

            source_id = hashlib.sha256(
                f"{first_time.isoformat()}_{queries[0]}".encode()
            ).hexdigest()[:16]

            entries.append(RawEntry(
                source_type="google_search",
                source_id=source_id,
                title=queries[0],
                content="\n".join(queries),
                created_at=first_time.isoformat(),
                updated_at=last_time.isoformat() if len(group) > 1 else None,
                source_metadata={
                    "query_count": len(queries),
                    "timespan_minutes": round(timespan, 1),
                },
            ))
        return entries
