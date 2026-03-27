"""
ファイルインポート CLI

Usage:
  python scripts/import_files.py <path> [options]

  --extensions / -e     対象拡張子を限定（例: -e .md .txt）
  --dry-run             実際にはDB投入せず、パース結果のみ表示
  --process             インポート後に処理パイプラインも実行

例:
  python scripts/import_files.py ~/notes/
  python scripts/import_files.py ~/notes/ -e .md .txt --process
  python scripts/import_files.py report.pdf --dry-run
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import load_settings
from src.db import ThoughtDB
from src.parsers.file_parser import FileParser, EXTRACTOR_MAP


def main():
    parser = argparse.ArgumentParser(description="ファイルをThought OSにインポート")
    parser.add_argument("path", help="ファイルまたはディレクトリのパス")
    parser.add_argument("-e", "--extensions", nargs="+", metavar="EXT",
                        help="対象拡張子（例: .md .txt .pdf）")
    parser.add_argument("--dry-run", action="store_true",
                        help="DB投入せずパース結果のみ表示")
    parser.add_argument("--process", action="store_true",
                        help="インポート後に要約・タグ・embedding処理も実行")
    args = parser.parse_args()

    target = Path(args.path).expanduser()
    if not target.exists():
        print(f"エラー: パスが存在しません: {target}")
        sys.exit(1)

    settings = load_settings()
    file_parser = FileParser()

    # 拡張子フィルタ
    if args.extensions:
        allowed = {e if e.startswith(".") else f".{e}" for e in args.extensions}
        unknown = allowed - set(EXTRACTOR_MAP.keys())
        if unknown:
            print(f"⚠ 未対応の拡張子: {', '.join(unknown)}")
        allowed &= set(EXTRACTOR_MAP.keys())
        if not allowed:
            print("対象拡張子がありません")
            sys.exit(1)
    else:
        allowed = None

    print(f"スキャン中: {target}")
    entries = file_parser.parse(target)

    # 拡張子フィルタ適用
    if allowed:
        entries = [e for e in entries
                   if any(e.source_metadata.get("file_name", "").endswith(ext) for ext in allowed)]

    if not entries:
        print("対象ファイルが見つかりません")
        return

    print(f"\n{len(entries)}件のエントリを検出:\n")

    if args.dry_run:
        for e in entries:
            meta = e.source_metadata
            print(f"  [{e.source_type}] {e.title}")
            print(f"    ファイル: {meta.get('file_name')} ({meta.get('file_size_bytes', 0):,} bytes)")
            print(f"    文字数: {meta.get('char_count', 0):,}  日時: {e.created_at[:10] if e.created_at else '不明'}")
            fh = e.file_hash[:12] + "..." if e.file_hash else "N/A"
            print(f"    hash: {fh}")
            print()
        print("（--dry-run モード: DB投入なし）")
        return

    # DB投入
    db = ThoughtDB(settings.database_url)
    inserted = 0
    skipped = 0
    for e in entries:
        entry_dict = {
            "source_type": e.source_type,
            "source_id": e.source_id,
            "title": e.title,
            "content": e.content,
            "summary": None,
            "category": None,
            "tags": [],
            "thinking_pattern": None,
            "embedding": None,
            "source_metadata": json.dumps(e.source_metadata),
            "created_at": e.created_at,
            "updated_at": e.updated_at,
            "file_hash": e.file_hash,
        }
        result = db.insert_entry(entry_dict)
        if result:
            inserted += 1
            print(f"  ✓ {e.title[:60]}")
        else:
            skipped += 1
            print(f"  ─ スキップ（重複）: {e.title[:60]}")

    print(f"\n投入: {inserted}件 / スキップ: {skipped}件")

    if args.process and inserted > 0:
        print("\n処理パイプライン実行中...")
        from src.pipeline import Pipeline
        pipeline = Pipeline(settings)
        result = pipeline.run_process_unprocessed()
        print(f"処理完了: {result['entries_processed']}件, "
              f"スキップ: {result.get('entries_skipped', 0)}件, "
              f"セッション: {result['sessions_created']}件")
        cost = result.get("cost", {})
        if cost:
            print(f"コスト: ${cost.get('estimated_cost_usd', 0)}")


if __name__ == "__main__":
    main()
