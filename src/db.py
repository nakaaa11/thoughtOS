import psycopg
from pgvector.psycopg import register_vector


class ThoughtDB:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self._conn = None

    def _get_conn(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self.database_url)
            register_vector(self._conn)
        return self._conn

    def close(self):
        if self._conn and not self._conn.closed:
            self._conn.close()

    def insert_entry(self, entry: dict) -> str | None:
        """エントリを挿入。重複時はスキップ。戻り値: UUID文字列 or None"""
        conn = self._get_conn()
        row = conn.execute(
            """
            INSERT INTO thought_entries
                (source_type, source_id, title, content, summary, category,
                 tags, thinking_pattern, embedding, source_metadata,
                 created_at, updated_at)
            VALUES
                (%(source_type)s, %(source_id)s, %(title)s, %(content)s,
                 %(summary)s, %(category)s, %(tags)s, %(thinking_pattern)s,
                 %(embedding)s, %(source_metadata)s,
                 %(created_at)s, %(updated_at)s)
            ON CONFLICT (source_type, source_id) DO NOTHING
            RETURNING id
            """,
            entry,
        ).fetchone()
        conn.commit()
        return str(row[0]) if row else None

    def insert_session(self, session: dict) -> str:
        """セッションを挿入。戻り値: UUID文字列"""
        conn = self._get_conn()
        row = conn.execute(
            """
            INSERT INTO thinking_sessions
                (entry_ids, sources, timeframe_start, timeframe_end,
                 topic, narrative, tags, embedding)
            VALUES
                (%(entry_ids)s, %(sources)s, %(timeframe_start)s, %(timeframe_end)s,
                 %(topic)s, %(narrative)s, %(tags)s, %(embedding)s)
            RETURNING id
            """,
            session,
        ).fetchone()
        conn.commit()
        return str(row[0])

    def get_entry(self, entry_id: str) -> dict | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM thought_entries WHERE id = %s", (entry_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_dict(row, conn.execute(
            "SELECT * FROM thought_entries WHERE id = %s", (entry_id,)
        ).description)

    def search_by_keyword(
        self,
        query: str,
        limit: int = 10,
        source_type: str | None = None,
        category: str | None = None,
    ) -> list[dict]:
        conn = self._get_conn()
        conditions = ["(title % %(query)s OR summary % %(query)s)"]
        params: dict = {"query": query, "limit": limit}

        if source_type:
            conditions.append("source_type = %(source_type)s")
            params["source_type"] = source_type
        if category:
            conditions.append("category = %(category)s")
            params["category"] = category

        where = " AND ".join(conditions)
        sql = f"""
            SELECT *, similarity(title, %(query)s) + similarity(COALESCE(summary, ''), %(query)s) AS score
            FROM thought_entries
            WHERE {where}
            ORDER BY score DESC
            LIMIT %(limit)s
        """
        rows = conn.execute(sql, params).fetchall()
        desc = conn.execute(f"SELECT *, 0 AS score FROM thought_entries LIMIT 0").description
        return [self._row_to_dict(r, desc) for r in rows]

    def search_by_similarity(
        self,
        embedding: list[float],
        limit: int = 10,
        source_type: str | None = None,
    ) -> list[dict]:
        conn = self._get_conn()
        params: dict = {"embedding": embedding, "limit": limit}
        where = ""
        if source_type:
            where = "WHERE source_type = %(source_type)s"
            params["source_type"] = source_type

        sql = f"""
            SELECT *, 1 - (embedding <=> %(embedding)s::vector) AS score
            FROM thought_entries
            {where}
            ORDER BY embedding <=> %(embedding)s::vector
            LIMIT %(limit)s
        """
        rows = conn.execute(sql, params).fetchall()
        desc = conn.execute(f"SELECT *, 0 AS score FROM thought_entries LIMIT 0").description
        return [self._row_to_dict(r, desc) for r in rows]

    def search_sessions_by_similarity(
        self, embedding: list[float], limit: int = 10
    ) -> list[dict]:
        conn = self._get_conn()
        sql = """
            SELECT *, 1 - (embedding <=> %(embedding)s::vector) AS score
            FROM thinking_sessions
            ORDER BY embedding <=> %(embedding)s::vector
            LIMIT %(limit)s
        """
        rows = conn.execute(sql, {"embedding": embedding, "limit": limit}).fetchall()
        desc = conn.execute("SELECT *, 0 AS score FROM thinking_sessions LIMIT 0").description
        return [self._row_to_dict(r, desc) for r in rows]

    def browse_by_tag(self, tag: str, limit: int = 20) -> list[dict]:
        conn = self._get_conn()
        sql = """
            SELECT * FROM thought_entries
            WHERE %(tag)s = ANY(tags)
            ORDER BY created_at DESC
            LIMIT %(limit)s
        """
        rows = conn.execute(sql, {"tag": tag, "limit": limit}).fetchall()
        desc = conn.execute("SELECT * FROM thought_entries LIMIT 0").description
        return [self._row_to_dict(r, desc) for r in rows]

    def browse_by_period(
        self, start: str, end: str, limit: int = 50
    ) -> list[dict]:
        conn = self._get_conn()
        sql = """
            SELECT * FROM thought_entries
            WHERE created_at >= %(start)s AND created_at < %(end)s
            ORDER BY created_at
            LIMIT %(limit)s
        """
        rows = conn.execute(sql, {"start": start, "end": end, "limit": limit}).fetchall()
        desc = conn.execute("SELECT * FROM thought_entries LIMIT 0").description
        return [self._row_to_dict(r, desc) for r in rows]

    def get_all_tags(self) -> list[dict]:
        conn = self._get_conn()
        sql = """
            SELECT tag, COUNT(*) as count
            FROM thought_entries, unnest(tags) AS tag
            GROUP BY tag
            ORDER BY count DESC
        """
        rows = conn.execute(sql).fetchall()
        return [{"tag": r[0], "count": r[1]} for r in rows]

    def get_unprocessed_entries(self) -> list[dict]:
        conn = self._get_conn()
        sql = "SELECT * FROM thought_entries WHERE summary IS NULL ORDER BY created_at"
        rows = conn.execute(sql).fetchall()
        desc = conn.execute("SELECT * FROM thought_entries LIMIT 0").description
        return [self._row_to_dict(r, desc) for r in rows]

    def update_entry(self, entry_id: str, updates: dict) -> None:
        conn = self._get_conn()
        set_clauses = []
        params = {"id": entry_id}
        for key, value in updates.items():
            set_clauses.append(f"{key} = %({key})s")
            params[key] = value

        sql = f"UPDATE thought_entries SET {', '.join(set_clauses)} WHERE id = %(id)s"
        conn.execute(sql, params)
        conn.commit()

    def _row_to_dict(self, row, description) -> dict:
        if description is None:
            return {}
        columns = [col.name for col in description]
        # row might have more columns than description (e.g. score)
        result = {}
        for i, val in enumerate(row):
            if i < len(columns):
                result[columns[i]] = val
            else:
                result[f"col_{i}"] = val
        return result
