"""パイプライン実行CLI"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import load_settings
from src.db import ThoughtDB
from src.embedder import Embedder
from src.pipeline import Pipeline
from src.parsers.chrome_history_parser import ChromeHistoryParser, find_chrome_history_db


def cmd_parse(args, settings):
    """パースしてDBに投入（API不使用）"""
    pipeline = Pipeline(settings)
    input_path = Path(args.input_path)
    if not input_path.exists():
        print(f"エラー: {input_path} が見つかりません")
        sys.exit(1)

    count = pipeline.run_parse_only([input_path])
    print(f"完了: {count}件をDBに投入しました")


def cmd_process(args, settings):
    """未処理エントリをClaude API + Voyage AIで処理"""
    pipeline = Pipeline(settings)
    result = pipeline.run_process_unprocessed()
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


def cmd_sessions(args, settings):
    """思考セッションの生成/更新"""
    pipeline = Pipeline(settings)
    db = ThoughtDB(settings.database_url)

    entries = db.browse_by_period("1900-01-01", "2100-01-01", limit=10000)
    with_emb = [e for e in entries if e.get("embedding") is not None]

    sessions = pipeline.session_builder.build_sessions(with_emb)
    for session in sessions:
        db.insert_session(session)

    print(f"完了: {len(sessions)}セッションを生成しました")


def cmd_full(args, settings):
    """parse + process + sessions を一括実行"""
    pipeline = Pipeline(settings)
    input_path = Path(args.input_path)
    if not input_path.exists():
        print(f"エラー: {input_path} が見つかりません")
        sys.exit(1)

    result = pipeline.run_full([input_path])
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


def cmd_stats(args, settings):
    """DB内のエントリ統計を表示"""
    db = ThoughtDB(settings.database_url)
    conn = db._get_conn()

    # 全体数
    total = conn.execute("SELECT COUNT(*) FROM thought_entries").fetchone()[0]
    print(f"総エントリ数: {total}")

    # ソースタイプ別
    rows = conn.execute(
        "SELECT source_type, COUNT(*) FROM thought_entries GROUP BY source_type ORDER BY COUNT(*) DESC"
    ).fetchall()
    print("\nソースタイプ別:")
    for row in rows:
        print(f"  {row[0]}: {row[1]}件")

    # カテゴリ別
    rows = conn.execute(
        "SELECT category, COUNT(*) FROM thought_entries WHERE category IS NOT NULL GROUP BY category ORDER BY COUNT(*) DESC"
    ).fetchall()
    if rows:
        print("\nカテゴリ別:")
        for row in rows:
            print(f"  {row[0]}: {row[1]}件")

    # 未処理
    unprocessed = conn.execute(
        "SELECT COUNT(*) FROM thought_entries WHERE summary IS NULL"
    ).fetchone()[0]
    print(f"\n未処理エントリ: {unprocessed}件")

    # セッション
    sessions = conn.execute("SELECT COUNT(*) FROM thinking_sessions").fetchone()[0]
    print(f"セッション数: {sessions}")

    # タグトップ10
    tags = db.get_all_tags()[:10]
    if tags:
        print("\nトップ10タグ:")
        for t in tags:
            print(f"  {t['tag']}: {t['count']}件")


def cmd_search(args, settings):
    """CLIから検索テスト"""
    db = ThoughtDB(settings.database_url)
    embedder = Embedder(settings.voyage_api_key, settings.voyage_model)

    query = args.query
    print(f"検索: {query}\n")

    # キーワード検索
    keyword_results = db.search_by_keyword(query, limit=5)
    if keyword_results:
        print("--- キーワード検索結果 ---")
        for r in keyword_results:
            print(f"  [{r.get('source_type')}] {r.get('title')}")
            print(f"    {r.get('summary', 'N/A')}")
            print()

    # ベクトル検索
    try:
        query_emb = embedder.embed(query)
        vector_results = db.search_by_similarity(query_emb, limit=5)
        if vector_results:
            print("--- ベクトル検索結果 ---")
            for r in vector_results:
                print(f"  [{r.get('source_type')}] {r.get('title')}")
                print(f"    {r.get('summary', 'N/A')}")
                print()
    except Exception as e:
        print(f"ベクトル検索エラー: {e}")


def cmd_fetch_chrome(args, settings):
    """Chrome履歴DBから本日分を取得してDBに投入"""
    from datetime import datetime, timezone, timedelta

    db_path = Path(args.db_path) if args.db_path else find_chrome_history_db()
    if db_path is None or not db_path.exists():
        print("エラー: Chrome履歴DBが見つかりません。--db-path で指定してください。")
        sys.exit(1)

    parser = ChromeHistoryParser(time_window_minutes=settings.session_time_window_minutes)

    # 取得範囲
    now = datetime.now(tz=timezone.utc)
    if args.days > 1:
        start = (now - timedelta(days=args.days - 1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    else:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    print(f"Chrome履歴取得: {start.strftime('%Y-%m-%d %H:%M')} 〜 {now.strftime('%Y-%m-%d %H:%M')}")
    entries = parser.parse_range(start, now, db_path)
    print(f"  取得: ブラウズ+検索 合計 {len(entries)} エントリ")

    # DBへ投入
    pipeline = Pipeline(settings)
    db = ThoughtDB(settings.database_url)
    inserted = 0
    import json as _json
    for entry in entries:
        result = db.insert_entry({
            "source_type": entry.source_type,
            "source_id": entry.source_id,
            "title": entry.title,
            "content": entry.content,
            "summary": None,
            "category": None,
            "tags": [],
            "thinking_pattern": None,
            "embedding": None,
            "source_metadata": _json.dumps(entry.source_metadata),
            "created_at": entry.created_at,
            "updated_at": entry.updated_at,
        })
        if result:
            inserted += 1

    print(f"  DB投入: {inserted}件（重複スキップ: {len(entries) - inserted}件）")

    if not args.parse_only:
        print("処理中（要約・カテゴリ分類・embedding）...")
        result = pipeline.run_process_unprocessed()
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    else:
        print("--parse-only 指定のため処理はスキップしました。")


def main():
    parser = argparse.ArgumentParser(description="Thought OS パイプライン")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # parse
    p_parse = subparsers.add_parser("parse", help="パースしてDBに投入")
    p_parse.add_argument("input_path", help="入力ファイルパス")
    p_parse.add_argument("--source", choices=["claude", "google_search", "google_browse"])

    # process
    subparsers.add_parser("process", help="未処理エントリを処理")

    # sessions
    subparsers.add_parser("sessions", help="思考セッションを生成")

    # full
    p_full = subparsers.add_parser("full", help="全ステップ一括実行")
    p_full.add_argument("input_path", help="入力ファイルパス")
    p_full.add_argument("--source", default="auto")

    # stats
    subparsers.add_parser("stats", help="DB統計を表示")

    # search
    # fetch_chrome
    p_chrome = subparsers.add_parser("fetch_chrome", help="Chrome履歴から本日分を自動取得")
    p_chrome.add_argument("--db-path", default=None, help="Chrome履歴DBのパス（省略時は自動検出）")
    p_chrome.add_argument("--days", type=int, default=1, help="取得する日数（デフォルト: 1=本日のみ）")
    p_chrome.add_argument("--parse-only", action="store_true", help="DB投入のみ（API処理をスキップ）")

    # search
    p_search = subparsers.add_parser("search", help="検索テスト")
    p_search.add_argument("query", help="検索クエリ")

    args = parser.parse_args()
    settings = load_settings()

    commands = {
        "parse": cmd_parse,
        "process": cmd_process,
        "sessions": cmd_sessions,
        "full": cmd_full,
        "stats": cmd_stats,
        "search": cmd_search,
        "fetch_chrome": cmd_fetch_chrome,
    }

    commands[args.command](args, settings)


if __name__ == "__main__":
    main()
