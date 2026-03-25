import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from .base import BaseParser, RawEntry

# デフォルトの時間窓（分）
DEFAULT_TIME_WINDOW = 30


class GoogleSearchParser(BaseParser):
    """Google検索履歴（Google Takeout）パーサー"""

    def __init__(self, time_window_minutes: int = DEFAULT_TIME_WINDOW):
        self.time_window_minutes = time_window_minutes

    def parse(self, input_path: str | Path) -> list[RawEntry]:
        input_path = Path(input_path)
        data = json.loads(input_path.read_text(encoding="utf-8"))

        queries = self._extract_queries(data)
        if not queries:
            return []

        # 時系列ソート
        queries.sort(key=lambda x: x["time"])

        # 時間窓でグループ化
        groups = self._group_by_time_window(queries)

        return [self._group_to_entry(group) for group in groups]

    def _extract_queries(self, data: list[dict]) -> list[dict]:
        queries = []
        for item in data:
            title = item.get("title", "")
            if not title.startswith("Searched for "):
                continue

            query_text = title.removeprefix("Searched for ")
            time_str = item.get("time", "")

            try:
                dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue

            queries.append({"query": query_text, "time": dt})
        return queries

    def _group_by_time_window(self, queries: list[dict]) -> list[list[dict]]:
        if not queries:
            return []

        groups = [[queries[0]]]
        for q in queries[1:]:
            last = groups[-1][-1]
            diff = (q["time"] - last["time"]).total_seconds() / 60
            if diff <= self.time_window_minutes:
                groups[-1].append(q)
            else:
                groups.append([q])
        return groups

    def _group_to_entry(self, group: list[dict]) -> RawEntry:
        queries_text = [q["query"] for q in group]
        first_time = group[0]["time"]
        last_time = group[-1]["time"]
        timespan = (last_time - first_time).total_seconds() / 60

        # source_id: 最初のクエリと時刻からハッシュ生成
        id_source = f"{first_time.isoformat()}_{queries_text[0]}"
        source_id = hashlib.sha256(id_source.encode()).hexdigest()[:16]

        return RawEntry(
            source_type="google_search",
            source_id=source_id,
            title=queries_text[0],
            content="\n".join(queries_text),
            created_at=first_time.isoformat(),
            updated_at=last_time.isoformat() if len(group) > 1 else None,
            source_metadata={
                "query_count": len(queries_text),
                "timespan_minutes": round(timespan, 1),
            },
        )
