CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ============================================================
-- thought_entries: 全データソース共通の思考エントリ
-- ============================================================
CREATE TABLE thought_entries (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_type VARCHAR(50) NOT NULL,
    source_id VARCHAR(255),
    title TEXT NOT NULL,
    content TEXT,
    summary TEXT,
    category VARCHAR(100),
    tags TEXT[] DEFAULT '{}',
    thinking_pattern JSONB,
    embedding vector(1024),
    source_metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ,
    processed_at TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- thinking_sessions: 思考セッション
-- ============================================================
CREATE TABLE thinking_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entry_ids UUID[] NOT NULL,
    sources TEXT[] NOT NULL,
    timeframe_start TIMESTAMPTZ NOT NULL,
    timeframe_end TIMESTAMPTZ NOT NULL,
    topic TEXT NOT NULL,
    narrative TEXT NOT NULL,
    tags TEXT[] DEFAULT '{}',
    embedding vector(1024),
    created_at TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- インデックス
-- ============================================================

-- キーワード検索（日本語対応）
CREATE INDEX idx_entries_title_trgm ON thought_entries USING gin(title gin_trgm_ops);
CREATE INDEX idx_entries_summary_trgm ON thought_entries USING gin(summary gin_trgm_ops);

-- カテゴリ・タグ絞り込み
CREATE INDEX idx_entries_category ON thought_entries(category);
CREATE INDEX idx_entries_tags ON thought_entries USING gin(tags);
CREATE INDEX idx_entries_source_type ON thought_entries(source_type);

-- 重複防止
CREATE UNIQUE INDEX idx_entries_source_unique ON thought_entries(source_type, source_id);

-- ファイルインポート重複防止（file_hash が設定されている場合のみ）
CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_file_hash
    ON thought_entries(file_hash) WHERE file_hash IS NOT NULL;

-- ベクトル類似検索
CREATE INDEX idx_entries_embedding ON thought_entries
    USING hnsw(embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX idx_sessions_embedding ON thinking_sessions
    USING hnsw(embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

-- 時系列
CREATE INDEX idx_entries_created ON thought_entries(created_at);
CREATE INDEX idx_sessions_timeframe ON thinking_sessions(timeframe_start, timeframe_end);
