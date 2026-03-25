"""DB初期化スクリプト: Docker起動確認 + init.sql実行 + 接続テスト"""

import subprocess
import sys
import time
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.config import load_settings


def check_docker():
    """Docker Composeでpostgresが起動しているか確認"""
    result = subprocess.run(
        ["docker", "compose", "ps", "--format", "json"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )
    if result.returncode != 0:
        print("Docker Composeが起動していません。先に docker-compose up -d を実行してください。")
        return False
    print("Docker Compose: OK")
    return True


def wait_for_db(database_url: str, max_retries: int = 10):
    """DBへの接続を待機"""
    for i in range(max_retries):
        try:
            with psycopg.connect(database_url) as conn:
                conn.execute("SELECT 1")
                print("DB接続: OK")
                return True
        except psycopg.OperationalError:
            print(f"DB接続待機中... ({i + 1}/{max_retries})")
            time.sleep(2)
    print("DB接続に失敗しました。")
    return False


def run_init_sql(database_url: str):
    """init.sqlを実行"""
    init_sql_path = Path(__file__).parent.parent / "db" / "init.sql"
    sql = init_sql_path.read_text(encoding="utf-8")

    with psycopg.connect(database_url) as conn:
        conn.execute(sql)
        conn.commit()
    print("init.sql実行: OK")


def verify_tables(database_url: str):
    """テーブルの存在を確認"""
    with psycopg.connect(database_url) as conn:
        result = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()
        tables = [row[0] for row in result]
        print(f"作成済みテーブル: {tables}")
        assert "thought_entries" in tables, "thought_entries テーブルが見つかりません"
        assert "thinking_sessions" in tables, "thinking_sessions テーブルが見つかりません"
    print("テーブル確認: OK")


def main():
    settings = load_settings()

    if not check_docker():
        sys.exit(1)

    if not wait_for_db(settings.database_url):
        sys.exit(1)

    run_init_sql(settings.database_url)
    verify_tables(settings.database_url)
    print("\nDB初期化が完了しました。")


if __name__ == "__main__":
    main()
