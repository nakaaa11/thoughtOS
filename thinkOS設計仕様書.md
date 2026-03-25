# Thought OS v2 — 実装仕様書
# Claude Codeへの指示: この文書に従って実装してください

---

## 0. プロジェクト概要

**目的:** 自分の思考プロセスをClaude に移植するための統合ナレッジベースを構築する。Claude会話履歴・Google検索/ブラウザ履歴を統合し、MCPサーバー経由で「第二の脳」として検索・呼び出し可能にする。

**最終的な3つのゴール（優先順）:**
1. C: 第二の脳 — 過去の思考を検索・呼び出し（Phase 1-2で実現）
2. B: 自分の鏡 — 思考パターンの可視化・傾向分析（Phase 3）
3. A: 自分の分身 — 自分的視点での代理回答（Phase 4）

**本仕様書のスコープ:** Phase 1-2（データ取得→蓄積→MCP検索）

---

## 1. ディレクトリ構成

```
thought-os/
├── README.md
├── pyproject.toml
├── .env.example
├── .env                          # ローカル設定（git管理外）
├── .gitignore
├── docker-compose.yml            # PostgreSQL + pgvector
├── db/
│   └── init.sql                  # DDL（テーブル・インデックス作成）
├── src/
│   ├── __init__.py
│   ├── config.py                 # 設定管理
│   ├── db.py                     # DB接続・CRUD・検索クエリ
│   ├── embedder.py               # Voyage AI クライアント
│   ├── claude_client.py          # Claude API ラッパー
│   ├── parsers/
│   │   ├── __init__.py
│   │   ├── base.py               # パーサー基底クラス
│   │   ├── claude_parser.py      # Claude会話JSONパーサー
│   │   ├── google_search_parser.py   # Google検索履歴パーサー
│   │   └── google_browse_parser.py   # Googleブラウザ履歴パーサー
│   ├── processors/
│   │   ├── __init__.py
│   │   ├── categorizer.py        # カテゴリ分類
│   │   ├── summarizer.py         # 要約 + タグ生成
│   │   ├── pattern_extractor.py  # 思考パターン抽出（Claude会話のみ）
│   │   └── session_builder.py    # 思考セッション統合
│   └── pipeline.py               # パイプライン統括
├── mcp_server/
│   ├── __init__.py
│   └── server.py                 # MCPサーバー（ツール定義）
├── prompts/
│   ├── categorize.txt
│   ├── summarize.txt
│   ├── extract_pattern.txt
│   └── build_session.txt
├── scripts/
│   ├── run_pipeline.py           # パイプライン実行CLI
│   ├── setup_db.py               # DB初期化スクリプト
│   └── run_mcp.py                # MCPサーバー起動
├── data/
│   ├── raw/                      # エクスポート生データ（git管理外）
│   └── processed/                # パース済み中間データ（git管理外）
└── tests/
    ├── test_parsers.py
    ├── test_processors.py
    └── fixtures/                  # テスト用サンプルデータ
        ├── claude_sample.json
        ├── google_search_sample.json
        └── google_browse_sample.json
```

---

## 2. 技術スタック・依存パッケージ

### pyproject.toml

```toml
[project]
name = "thought-os"
version = "0.2.0"
description = "思考プロセス統合ナレッジベース"
requires-python = ">=3.12"
dependencies = [
    "anthropic>=0.40.0",
    "voyageai>=0.3.0",
    "psycopg[binary]>=3.2",
    "pgvector>=0.3",
    "python-dotenv>=1.0",
    "mcp>=1.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
]
web = [
    "fastapi>=0.115",
    "uvicorn>=0.32",
]
```

### docker-compose.yml

```yaml
services:
  db:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: thought_os
      POSTGRES_PASSWORD: thought_os_dev
      POSTGRES_DB: thought_os
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./db/init.sql:/docker-entrypoint-initdb.d/init.sql

volumes:
  pgdata:
```

### .env.example

```env
# Claude API
ANTHROPIC_API_KEY=sk-ant-xxxxx

# Voyage AI
VOYAGE_API_KEY=pa-xxxxx

# PostgreSQL
DATABASE_URL=postgresql://thought_os:thought_os_dev@localhost:5432/thought_os

# モデル設定
CLAUDE_MODEL=claude-sonnet-4-20250514
VOYAGE_MODEL=voyage-3
EMBEDDING_DIMENSIONS=1024

# パイプライン設定
BATCH_SIZE=10
RATE_LIMIT_DELAY=1.0
MIN_TURNS_FOR_ANALYSIS=3
SESSION_TIME_WINDOW_MINUTES=30
SESSION_SIMILARITY_THRESHOLD=0.7
```

---

## 3. データベーススキーマ

### db/init.sql

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- thought_entries: 全データソース共通の思考エントリ
-- 1レコード = 1つの思考単位
--   Claude会話 → 1会話 = 1レコード
--   Google検索 → テーマ単位にグループ化した検索群 = 1レコード
--   Googleブラウザ → テーマ単位にグループ化した閲覧群 = 1レコード
-- ============================================================
CREATE TABLE thought_entries (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_type VARCHAR(50) NOT NULL,
    -- 有効値: "claude", "google_search", "google_browse"
    -- 将来追加: "youtube", "github", "kindle", "notion",
    --          "slack", "calendar", "gmail", "twitter",
    --          "spotify", "zenn", "qiita", "healthkit"
    source_id VARCHAR(255),
    -- 元データの一意ID（Claude会話UUID、URLハッシュ等）
    -- 重複投入防止に使用
    title TEXT NOT NULL,
    -- Claude: 会話タイトル
    -- Google検索: 代表的な検索クエリ
    -- Googleブラウザ: グループのトピック名
    content TEXT,
    -- Claude: 会話全文（Human: ... Assistant: ... の形式）
    -- Google検索: 検索クエリ群（改行区切り）
    -- Googleブラウザ: URL群（改行区切り）
    summary TEXT,
    -- Claude APIで生成した1-3文の要約
    category VARCHAR(100),
    -- 分類カテゴリ:
    --   "技術学習", "キャリア・仕事", "アプリ開発",
    --   "文書・コミュニケーション", "思考整理・ブレスト",
    --   "調査・リサーチ", "その他"
    tags TEXT[] DEFAULT '{}',
    -- タグ配列（例: ["FSI", "AI", "FX"]）
    thinking_pattern JSONB,
    -- Claude会話のみ。他ソースはNULL。
    -- 構造:
    -- {
    --   "question_style": "質問の立て方の傾向",
    --   "deepening_points": ["深掘りポイント"],
    --   "decision_criteria": ["判断基準"],
    --   "recurring_themes": ["繰り返しテーマ"],
    --   "thinking_habits": ["思考の癖"],
    --   "values_expressed": ["価値観"],
    --   "knowledge_gaps": ["知識ギャップ"],
    --   "action_tendency": "行動傾向"
    -- }
    embedding vector(1024),
    -- Voyage AI (voyage-3) でsummaryから生成
    source_metadata JSONB DEFAULT '{}',
    -- ソース固有の追加情報
    -- Claude: {"turn_count": 10, "human_char_count": 5000, "model": "sonnet"}
    -- Google検索: {"query_count": 3, "timespan_minutes": 25}
    -- Googleブラウザ: {"urls": [...], "visit_count": 5, "timespan_minutes": 20}
    created_at TIMESTAMPTZ NOT NULL,
    -- 元データの作成日時
    updated_at TIMESTAMPTZ,
    -- 元データの更新日時
    processed_at TIMESTAMPTZ DEFAULT now()
    -- パイプライン処理日時
);

-- ============================================================
-- thinking_sessions: 思考セッション
-- 時間的に近接し、トピックが類似するエントリを統合した上位レイヤー
-- 複数ソースを横断して「あの時何を考えていたか」を再構成する
-- ============================================================
CREATE TABLE thinking_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entry_ids UUID[] NOT NULL,
    -- 紐づくthought_entries.id の配列
    sources TEXT[] NOT NULL,
    -- 含まれるソース種別（例: ["claude", "google_search"]）
    timeframe_start TIMESTAMPTZ NOT NULL,
    timeframe_end TIMESTAMPTZ NOT NULL,
    topic TEXT NOT NULL,
    -- 統合トピック（Claude APIで生成）
    narrative TEXT NOT NULL,
    -- 思考の流れのストーリー（Claude APIで生成）
    -- 例: "デコレータの基本をGoogle検索とRealPythonで学習後、
    --      クラスへの適用方法でつまずきClaudeに相談して解決。
    --      その後functools.wrapsの必要性まで深掘りした。"
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

-- ベクトル類似検索（データ量が少ない初期はHNSWが適切）
CREATE INDEX idx_entries_embedding ON thought_entries
    USING hnsw(embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX idx_sessions_embedding ON thinking_sessions
    USING hnsw(embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

-- 時系列
CREATE INDEX idx_entries_created ON thought_entries(created_at);
CREATE INDEX idx_sessions_timeframe ON thinking_sessions(timeframe_start, timeframe_end);

-- pg_trgm拡張（日本語キーワード部分一致用）
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

---

## 4. 入力データフォーマット

### 4.1 Claude会話エクスポート（Chrome拡張経由）

```json
[
  {
    "uuid": "conversation-uuid",
    "name": "会話タイトル",
    "created_at": "2025-01-01T00:00:00Z",
    "updated_at": "2025-01-01T00:00:00Z",
    "model": "claude-3-5-sonnet-20241022",
    "chat_messages": [
      {
        "uuid": "message-uuid",
        "sender": "human",
        "text": "メッセージ本文",
        "created_at": "2025-01-01T00:00:00Z",
        "content": [],
        "attachments": [],
        "files": []
      }
    ]
  }
]
```

**注意:** Chrome拡張によっては `sender` の代わりに `role` を使用し、`role: "user"` となる場合がある。パーサーは両方に対応すること。`content` 配列に `{"type": "text", "text": "..."}` 形式でテキストが入る場合もある。

### 4.2 Google検索履歴（Google Takeout）

ファイルパス: `Takeout/My Activity/Search/MyActivity.json`

```json
[
  {
    "header": "Search",
    "title": "Searched for Python デコレータ 使い方",
    "titleUrl": "https://www.google.com/search?q=...",
    "time": "2025-01-15T10:00:00.000Z",
    "products": ["Search"]
  }
]
```

**パース仕様:**
- `title` から "Searched for " プレフィックスを除去して検索クエリを抽出
- 時間窓（`SESSION_TIME_WINDOW_MINUTES`、デフォルト30分）以内の連続クエリを1グループにまとめる
- 1グループ = 1 thought_entry

### 4.3 Googleブラウザ履歴（Google Takeout）

ファイルパス: `Takeout/Chrome/BrowserHistory.json`

```json
{
  "Browser History": [
    {
      "favicon_url": "...",
      "page_transition": "LINK",
      "title": "Python Decorators – Real Python",
      "url": "https://realpython.com/primer-on-decorators/",
      "client_id": "...",
      "time_usec": 1705312800000000
    }
  ]
}
```

**パース仕様:**
- `time_usec` はマイクロ秒のUNIXタイムスタンプ。`datetime.fromtimestamp(time_usec / 1_000_000)` で変換
- 検索エンジンのURL（google.com/search等）はフィルタリング（google_search側で扱う）
- 時間窓でグループ化し、グループ内のページタイトルの類似性でさらに分割
- 1グループ = 1 thought_entry

---

## 5. モジュール仕様

### 5.1 src/config.py

```python
"""
設定管理。.env から読み込み。
全設定を1つのSettingsクラスで管理する。
"""
from dataclasses import dataclass, field
from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Settings:
    # API keys
    anthropic_api_key: str
    voyage_api_key: str

    # Database
    database_url: str

    # Models
    claude_model: str = "claude-sonnet-4-20250514"
    voyage_model: str = "voyage-3"
    embedding_dimensions: int = 1024

    # Pipeline
    batch_size: int = 10
    rate_limit_delay: float = 1.0
    min_turns_for_analysis: int = 3
    session_time_window_minutes: int = 30
    session_similarity_threshold: float = 0.7

    # Paths
    data_dir: Path = Path("data")

# os.getenv()で初期化するファクトリ関数を用意
```

### 5.2 src/db.py

**責務:** PostgreSQL接続、CRUD操作、検索クエリの実行

**必須メソッド:**
```python
class ThoughtDB:
    def __init__(self, database_url: str): ...
    def insert_entry(self, entry: dict) -> str: ...
        # 戻り値: UUID文字列
        # source_type + source_id の重複はSKIP (ON CONFLICT DO NOTHING)
    def insert_session(self, session: dict) -> str: ...
    def get_entry(self, entry_id: str) -> dict | None: ...
    def search_by_keyword(self, query: str, limit: int = 10,
                          source_type: str | None = None,
                          category: str | None = None) -> list[dict]: ...
        # pg_trgmの部分一致検索をtitleとsummaryに対して実行
    def search_by_similarity(self, embedding: list[float], limit: int = 10,
                             source_type: str | None = None) -> list[dict]: ...
        # pgvectorのコサイン類似度検索
    def search_sessions_by_similarity(self, embedding: list[float],
                                       limit: int = 10) -> list[dict]: ...
    def browse_by_tag(self, tag: str, limit: int = 20) -> list[dict]: ...
    def browse_by_period(self, start: str, end: str, limit: int = 50) -> list[dict]: ...
    def get_all_tags(self) -> list[dict]: ...
        # タグとその出現回数を返す
    def get_unprocessed_entries(self) -> list[dict]: ...
        # summary が NULL のエントリを返す（パイプライン再実行用）
    def update_entry(self, entry_id: str, updates: dict) -> None: ...
        # 部分更新（summary, category, tags, embedding等）
```

**接続:** `psycopg` (v3) を使用。`pgvector.psycopg` でベクトル型を登録する。

### 5.3 src/embedder.py

**責務:** Voyage AI APIを使ったテキストのベクトル化

```python
class Embedder:
    def __init__(self, api_key: str, model: str = "voyage-3"): ...
    def embed(self, text: str) -> list[float]: ...
        # 単一テキスト → 1024次元ベクトル
    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...
        # バッチ処理（最大128テキスト/回）
```

**使用ライブラリ:** `voyageai` SDK

### 5.4 src/claude_client.py

**責務:** Claude API呼び出し、リトライ、トークン計測

```python
class ClaudeClient:
    def __init__(self, api_key: str, model: str): ...
    def query(self, prompt: str, system: str = "",
              max_retries: int = 3, json_mode: bool = False) -> str: ...
    def query_json(self, prompt: str, system: str = "") -> dict | list | None: ...
        # JSON出力をパースして返す。パース失敗時はNone
    def load_prompt(self, template_path: Path, **kwargs) -> str: ...
        # プロンプトテンプレート読み込み + 変数展開
    def usage_summary(self) -> dict: ...
        # {"input_tokens": int, "output_tokens": int, "estimated_cost_usd": float}
```

**リトライ:** RateLimitErrorは指数バックオフ（2, 4, 8秒）。JSONパース失敗もリトライ。

### 5.5 src/parsers/base.py

```python
from dataclasses import dataclass
from typing import Any

@dataclass
class RawEntry:
    """パーサーの共通出力フォーマット"""
    source_type: str
    source_id: str
    title: str
    content: str
    created_at: str        # ISO 8601
    updated_at: str | None
    source_metadata: dict[str, Any]

class BaseParser:
    """パーサー基底クラス"""
    def parse(self, input_path: str | Path) -> list[RawEntry]: ...
        # ファイル/ディレクトリ/ZIPを受け取り、RawEntryのリストを返す
```

### 5.6 src/parsers/claude_parser.py

**入力:** Chrome拡張エクスポートのJSON（単体ファイル / ディレクトリ / ZIP）
**出力:** `list[RawEntry]`

**仕様:**
- `sender` / `role` 両方のフィールドに対応
- `role: "user"` は `"human"` に正規化
- `text` フィールドと `content` 配列の両方からテキスト抽出
- `content` 内の `tool_use` は `[Tool: tool_name]` として記録
- 空メッセージ・空会話はスキップ
- content に会話全文を `"Human: ...\nAssistant: ...\n"` の形式で格納
- source_metadata に `turn_count`, `human_char_count`, `assistant_char_count`, `model` を格納

### 5.7 src/parsers/google_search_parser.py

**入力:** Google Takeout の `MyActivity.json`
**出力:** `list[RawEntry]`（テーマ単位でグループ化済み）

**グループ化アルゴリズム:**
1. 全エントリを時系列ソート
2. 先頭から走査し、前のエントリから `SESSION_TIME_WINDOW_MINUTES` 以内なら同じグループ
3. 時間窓を超えたら新グループ開始
4. 各グループの title = 最初の検索クエリ
5. 各グループの content = 全クエリを改行区切りで結合
6. source_metadata に `query_count`, `timespan_minutes` を格納

### 5.8 src/parsers/google_browse_parser.py

**入力:** Google Takeout の `BrowserHistory.json`
**出力:** `list[RawEntry]`（テーマ単位でグループ化済み）

**グループ化アルゴリズム:**
1. 検索エンジンURL（google.com/search, bing.com/search 等）をフィルタ
2. `time_usec` をdatetimeに変換し時系列ソート
3. 時間窓でグループ化（google_search_parserと同じロジック）
4. 各グループの title = 最も長いページタイトル（代表として）
5. 各グループの content = 全URLを改行区切りで結合
6. source_metadata に `urls`(配列), `visit_count`, `timespan_minutes` を格納

### 5.9 src/processors/categorizer.py

**入力:** `RawEntry` のリスト（バッチ）
**出力:** 各エントリの `category` 文字列

**仕様:**
- `BATCH_SIZE` 件ずつまとめてClaude APIに投げる
- プロンプトテンプレート: `prompts/categorize.txt`
- 出力: JSON配列 `[{"source_id": "...", "category": "..."}]`

### 5.10 src/processors/summarizer.py

**入力:** 1件の `RawEntry`
**出力:** `{"summary": str, "tags": list[str]}`

**仕様:**
- summaryは1-3文
- tagsは3-8個
- プロンプトテンプレート: `prompts/summarize.txt`
- 出力: JSON `{"summary": "...", "tags": ["...", "..."]}`

### 5.11 src/processors/pattern_extractor.py

**入力:** 1件の `RawEntry`（source_type == "claude" のみ）
**出力:** `thinking_pattern` の JSONB構造

**仕様:**
- `min_turns_for_analysis` 未満のターン数の会話はスキップ（NULLを返す）
- 長い会話（content > 10,000文字）は先にClaude APIで要約してから分析
- プロンプトテンプレート: `prompts/extract_pattern.txt`

### 5.12 src/processors/session_builder.py

**入力:** DB内の全 thought_entries（processed_at 以降の新規分も対応）
**出力:** thinking_sessions レコード

**アルゴリズム:**
```
1. 全entriesをcreated_atで時系列ソート
2. 時間窓（SESSION_TIME_WINDOW_MINUTES）でグループ候補作成
   - entry[i] と entry[i+1] の時間差が窓以内なら同グループ
3. 各グループ内でembeddingのコサイン類似度を計算
   - 全ペアの平均類似度が SESSION_SIMILARITY_THRESHOLD 以上 → 統合
   - 未満 → サブグループに分割（最も類似度が低いペアで分割）
4. 2件以上のエントリを含むグループのみセッション化
5. Claude APIに各セッションのエントリsummary群を渡してtopic + narrative生成
6. セッション全体のembedding = 全エントリembeddingの平均ベクトル
7. DBに保存
```

### 5.13 src/pipeline.py

**パイプライン全体の統括**

```python
class Pipeline:
    def __init__(self, settings: Settings): ...

    def run_full(self, input_paths: list[Path]) -> dict:
        """全ステップ実行"""
        # 1. パース
        # 2. DB投入（raw状態）
        # 3. 分類（バッチ）
        # 4. 要約 + タグ（1件ずつ）
        # 5. パターン抽出（Claudeのみ、1件ずつ）
        # 6. embedding生成（バッチ）
        # 7. セッション統合
        # 戻り値: {"entries_processed": int, "sessions_created": int, "cost": dict}

    def run_parse_only(self, input_paths: list[Path]) -> int:
        """パースしてDB投入のみ（API不使用）"""

    def run_process_unprocessed(self) -> dict:
        """未処理エントリのみ処理（差分処理用）"""
```

---

## 6. MCPサーバー仕様

### mcp_server/server.py

MCP SDK (`mcp` パッケージ) を使用してツールを定義する。

**提供ツール:**

#### search_thoughts

```
名前: search_thoughts
説明: 過去の思考・会話・検索をキーワードまたは意味的類似度で検索する
入力:
  query: str (必須) — 検索クエリ
  source_type: str (任意) — "claude", "google_search", "google_browse" で絞り込み
  category: str (任意) — カテゴリで絞り込み
  limit: int (任意, デフォルト5) — 最大件数
出力: エントリのリスト（title, summary, category, tags, created_at, source_type）
動作:
  1. queryをVoyage AIでembedding化
  2. ベクトル類似検索 + キーワード部分一致の両方を実行
  3. 結果をマージしスコア順で返す
```

#### search_sessions

```
名前: search_sessions
説明: 思考セッション（複数ソースを統合した思考の流れ）を検索する
入力:
  query: str (必須)
  limit: int (任意, デフォルト5)
出力: セッションのリスト（topic, narrative, sources, tags, timeframe_start, timeframe_end）
動作:
  1. queryをembedding化
  2. thinking_sessionsをベクトル検索
```

#### browse_by_tag

```
名前: browse_by_tag
説明: タグでエントリを絞り込む。引数なしでタグ一覧を返す
入力:
  tag: str (任意) — 指定時はそのタグのエントリを返す
  limit: int (任意, デフォルト20)
出力: タグ指定時はエントリリスト、未指定時はタグ名と件数のリスト
```

#### browse_by_period

```
名前: browse_by_period
説明: 期間を指定してエントリを閲覧する
入力:
  start: str (必須) — ISO 8601日付（例: "2025-01-01"）
  end: str (必須) — ISO 8601日付
  limit: int (任意, デフォルト50)
出力: 期間内のエントリリスト（時系列順）
```

#### get_thinking_pattern

```
名前: get_thinking_pattern
説明: 特定のClaude会話の思考パターン詳細を返す
入力:
  entry_id: str (必須) — thought_entryのUUID
出力: thinking_patternのJSON全体 + 会話要約
```

---

## 7. プロンプトテンプレート

### prompts/categorize.txt

```
以下のエントリリストを最も適切なカテゴリに分類してください。

カテゴリ:
- 技術学習（プログラミング、ツール、フレームワーク）
- キャリア・仕事（職場、プロジェクト、面談）
- アプリ開発（自作アプリ・ツールの設計/実装）
- 文書・コミュニケーション（メール添削、資料作成）
- 思考整理・ブレスト（アイデア整理、壁打ち）
- 調査・リサーチ（情報収集、比較検討）
- その他

エントリ:
{entries}

JSON配列のみ出力:
[{{"source_id": "...", "category": "カテゴリ名"}}]
```

### prompts/summarize.txt

```
以下のコンテンツを分析し、要約とタグを生成してください。

タイトル: {title}
ソース: {source_type}
内容:
{content}

JSON形式のみ出力:
{{
  "summary": "1-3文の要約。何を考え、何を調べ、何を決めたかを含める",
  "tags": ["タグ1", "タグ2", "タグ3"]
}}
```

### prompts/extract_pattern.txt

```
以下のユーザーとAIの会話を分析し、ユーザーの思考パターンを抽出してください。

タイトル: {title}
日時: {created_at}

会話:
{content}

JSON形式のみ出力:
{{
  "question_style": "質問の立て方の傾向を1-2文で",
  "deepening_points": ["深掘りしたポイント"],
  "decision_criteria": ["判断基準として現れたもの"],
  "recurring_themes": ["繰り返し現れるテーマ"],
  "thinking_habits": ["思考の癖"],
  "values_expressed": ["表出された価値観"],
  "knowledge_gaps": ["知識不足を感じた領域"],
  "action_tendency": "行動傾向（慎重/即断/構造化先行 等）"
}}
```

### prompts/build_session.txt

```
以下は同じ時間帯に行われた複数の活動です。
これらを統合し、ユーザーが何を考えていたかのストーリーを生成してください。

活動一覧:
{entries}

JSON形式のみ出力:
{{
  "topic": "統合トピック（10文字以内）",
  "narrative": "思考の流れを2-4文で。何をきっかけに、何を調べ、何で詰まり、どう解決したかを含める",
  "tags": ["統合タグ1", "統合タグ2"]
}}
```

---

## 8. CLI仕様

### scripts/run_pipeline.py

```
Usage:
  python scripts/run_pipeline.py parse <input_path> [--source claude|google_search|google_browse]
    → パースしてDBに投入（API不使用）

  python scripts/run_pipeline.py process [--limit N]
    → 未処理エントリをClaude API + Voyage AIで処理

  python scripts/run_pipeline.py sessions
    → 思考セッションの生成/更新

  python scripts/run_pipeline.py full <input_path> [--source auto]
    → parse + process + sessions を一括実行

  python scripts/run_pipeline.py stats
    → DB内のエントリ統計を表示

  python scripts/run_pipeline.py search <query>
    → CLIから検索テスト（MCP不要で動作確認）
```

### scripts/setup_db.py

```
Usage:
  python scripts/setup_db.py
    → Docker起動確認 + init.sql実行 + 接続テスト
```

### scripts/run_mcp.py

```
Usage:
  python scripts/run_mcp.py
    → MCPサーバー起動（stdioモード）
```

---

## 9. 実装順序

**Claude Codeへの指示: 以下の順番で実装してください。各ステップ完了後にテストを実行して動作確認すること。**

### Step 1: プロジェクト基盤
1. ディレクトリ構成を作成
2. pyproject.toml, docker-compose.yml, .env.example, .gitignore を作成
3. `src/config.py` を実装
4. `docker-compose up -d` で PostgreSQL起動 → `scripts/setup_db.py` で初期化

### Step 2: パーサー
1. `src/parsers/base.py` — RawEntry + BaseParser
2. `src/parsers/claude_parser.py` — テストフィクスチャで動作確認
3. `src/parsers/google_search_parser.py` — テストフィクスチャで動作確認
4. `src/parsers/google_browse_parser.py` — テストフィクスチャで動作確認

### Step 3: DB層
1. `db/init.sql` を作成
2. `src/db.py` を実装
3. パーサー出力をDBに投入するテスト

### Step 4: 処理パイプライン
1. `src/claude_client.py` を実装
2. `src/embedder.py` を実装
3. `src/processors/categorizer.py`
4. `src/processors/summarizer.py`
5. `src/processors/pattern_extractor.py`
6. `src/processors/session_builder.py`
7. `src/pipeline.py` で統括
8. 10件のテストデータで全パイプライン実行 → プロンプト品質確認

### Step 5: MCPサーバー
1. `mcp_server/server.py` — 全5ツールを定義
2. `scripts/run_mcp.py` で起動
3. Claudeとの接続テスト

### Step 6: CLI
1. `scripts/run_pipeline.py` の全コマンドを実装
2. `scripts/setup_db.py` を実装

---

## 10. テストフィクスチャ

`tests/fixtures/` にサンプルデータを配置:
- `claude_sample.json` — 5会話（公式形式 + 拡張形式の混在）
- `google_search_sample.json` — 20クエリ（3テーマ分）
- `google_browse_sample.json` — 30ページ閲覧（3テーマ分）

テストは `pytest` で実行。パーサーとDB層は外部API不要で単体テスト可能。
処理パイプラインのテストはモック or 少量データでのインテグレーションテスト。

---

## 11. 容量見積もり

| 項目 | 年間 | 10年 | 20年 |
|------|------|------|------|
| thought_entries（テキスト） | ~45MB | ~450MB | ~900MB |
| embedding（ベクトル） | ~62MB | ~620MB | ~1.2GB |
| thinking_sessions | ~25MB | ~250MB | ~500MB |
| インデックス | ~40MB | ~400MB | ~800MB |
| **合計** | **~172MB** | **~1.7GB** | **~3.4GB** |

PostgreSQLで20年運用しても3.4GB。パーティショニング不要。

---

## 12. コスト見積もり

### 初期処理（過去データ一括、500会話 + Google履歴想定）
- Claude API: ~$7
- Voyage AI: ~$0.01
- **合計: ~$7**

### 月額運用
- Claude API: ~$1-2
- インフラ（ローカルDocker）: $0
- **合計: ~$1-2/月**

---

## 13. 将来の拡張（本仕様書のスコープ外）

以下のデータソースは `source_type` を追加し、対応パーサーを `src/parsers/` に追加するだけで統合可能。テーブル構造の変更は不要。

| ソース | source_type | 取得方法 | パーソナリティ情報 |
|--------|-------------|---------|-------------------|
| YouTube視聴 | youtube | Google Takeout | 関心領域 |
| ブックマーク | bookmark | Chromeエクスポート | 長期的関心 |
| Kindleハイライト | kindle | エクスポート | 何に反応したか |
| Notion / Obsidian | notion | API / エクスポート | 構造化された思考 |
| Slack / Teams | slack | API / エクスポート | 仕事上の思考 |
| Googleカレンダー | calendar | Takeout / API | 時間の使い方 |
| Gmail | gmail | API | コミュニケーション傾向 |
| X / SNS | twitter | APIまたはアーカイブ | 発信=価値観 |
| Spotify / Podcast | spotify | API | 関心の傍証 |
| Zenn / Qiita | zenn | API | 技術的関心の発信 |
| GitHub | github | API | コード上の関心と行動 |
| HealthKit | healthkit | iOSエクスポート | 身体状態×思考 |

### モバイル統合: 日報アプリ（React Native + Django + Claude API）

別途開発中の日報リフレクションアプリとThought OSを統合する構想。日報アプリは HealthKit連携 + フリーミアムモデルで設計済み。

**統合パターン:**
- 日報アプリの Django バックエンドから Thought OS の PostgreSQL に日報エントリを投入
- `source_type = "daily_journal"` として thought_entries に格納
- source_metadata に `{"mood": "...", "health_data": {...}, "goals": [...]}` 等を格納

**統合によって得られる価値:**
- 日報の振り返り内容が「第二の脳」の検索対象になる
- Claude会話 + Google調査 + 日報が同日の thinking_session に統合され、「あの日何を考え、何を感じていたか」の全体像が再構成される
- HealthKitデータ（睡眠、運動量等）と思考パターンの相関分析が可能になる（B: 自分の鏡）
- 日報アプリ側からMCPサーバー経由で過去の思考を検索し、リフレクションの質を向上させる

**実装時の追加作業:**
- `src/parsers/journal_parser.py` の追加
- Django側にThought OS投入用のAPI or バッチ処理を追加
- テーブル構造の変更は不要
