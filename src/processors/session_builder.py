import math
from datetime import datetime, timedelta
from pathlib import Path

from ..claude_client import ClaudeClient
from ..embedder import Embedder

PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"


class SessionBuilder:
    def __init__(
        self,
        claude_client: ClaudeClient,
        embedder: Embedder,
        time_window_minutes: int = 30,
        similarity_threshold: float = 0.7,
    ):
        self.claude = claude_client
        self.embedder = embedder
        self.time_window_minutes = time_window_minutes
        self.similarity_threshold = similarity_threshold

    def build_sessions(self, entries: list[dict]) -> list[dict]:
        """DBのエントリからセッションを構築"""
        # embeddingがないエントリは除外
        entries_with_emb = [e for e in entries if e.get("embedding") is not None]
        if not entries_with_emb:
            return []

        # 時系列ソート
        entries_with_emb.sort(key=lambda e: e["created_at"])

        # 時間窓でグループ化
        groups = self._group_by_time_window(entries_with_emb)

        # 類似度でサブグループに分割
        sessions = []
        for group in groups:
            if len(group) < 2:
                continue
            subgroups = self._split_by_similarity(group)
            for sg in subgroups:
                if len(sg) >= 2:
                    sessions.append(sg)

        # 各セッションにtopic/narrativeを生成
        results = []
        for session_entries in sessions:
            session = self._build_session(session_entries)
            if session:
                results.append(session)

        return results

    def _group_by_time_window(self, entries: list[dict]) -> list[list[dict]]:
        if not entries:
            return []

        groups = [[entries[0]]]
        for entry in entries[1:]:
            last = groups[-1][-1]
            diff = (entry["created_at"] - last["created_at"]).total_seconds() / 60
            if diff <= self.time_window_minutes:
                groups[-1].append(entry)
            else:
                groups.append([entry])
        return groups

    def _split_by_similarity(self, group: list[dict]) -> list[list[dict]]:
        if len(group) <= 1:
            return [group]

        avg_sim = self._average_similarity(group)
        if avg_sim >= self.similarity_threshold:
            return [group]

        # 最も類似度が低いペアで分割
        min_sim = float("inf")
        split_idx = 1
        for i in range(len(group) - 1):
            sim = self._cosine_similarity(
                group[i]["embedding"], group[i + 1]["embedding"]
            )
            if sim < min_sim:
                min_sim = sim
                split_idx = i + 1

        left = group[:split_idx]
        right = group[split_idx:]

        return self._split_by_similarity(left) + self._split_by_similarity(right)

    def _average_similarity(self, group: list[dict]) -> float:
        if len(group) < 2:
            return 1.0

        total = 0.0
        count = 0
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                total += self._cosine_similarity(
                    group[i]["embedding"], group[j]["embedding"]
                )
                count += 1
        return total / count if count > 0 else 1.0

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def _build_session(self, entries: list[dict]) -> dict | None:
        entries_text = "\n".join(
            f"- [{e.get('source_type')}] {e.get('title')}: {e.get('summary', '(未要約)')}"
            for e in entries
        )

        prompt = self.claude.load_prompt(
            PROMPTS_DIR / "build_session.txt", entries=entries_text
        )

        result = self.claude.query_json(prompt)
        if not result:
            return None

        # embeddingの平均ベクトル
        embeddings = [e["embedding"] for e in entries if e.get("embedding") is not None]
        avg_embedding = self._average_embedding(embeddings) if embeddings else None

        entry_ids = [str(e["id"]) for e in entries]
        sources = list(set(e.get("source_type", "") for e in entries))
        times = [e["created_at"] for e in entries]

        return {
            "entry_ids": entry_ids,
            "sources": sources,
            "timeframe_start": min(times).isoformat(),
            "timeframe_end": max(times).isoformat(),
            "topic": result.get("topic", ""),
            "narrative": result.get("narrative", ""),
            "tags": result.get("tags", []),
            "embedding": avg_embedding,
        }

    def _average_embedding(self, embeddings: list[list[float]]) -> list[float]:
        if not embeddings:
            return []
        dim = len(embeddings[0])
        avg = [0.0] * dim
        for emb in embeddings:
            for i in range(dim):
                avg[i] += emb[i]
        return [v / len(embeddings) for v in avg]
