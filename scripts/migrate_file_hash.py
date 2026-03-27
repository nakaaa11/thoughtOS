"""file_hash カラムをthought_entriesテーブルに追加するマイグレーション"""
import psycopg

# nak superuser で接続してオーナー変更 + カラム追加
try:
    conn = psycopg.connect("postgresql://nak@localhost/thought_os")
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute("ALTER TABLE thought_entries OWNER TO thought_os")
    print("ownership transferred to thought_os")

    cur.execute("ALTER TABLE thinking_sessions OWNER TO thought_os")
    print("thinking_sessions ownership transferred")

    conn.close()
except Exception as e:
    print(f"ownership change: {e}")

# thought_os で接続してカラム追加
try:
    conn2 = psycopg.connect("postgresql://thought_os:thought_os_dev@localhost:5432/thought_os")
    conn2.autocommit = True
    cur2 = conn2.cursor()

    cur2.execute("ALTER TABLE thought_entries ADD COLUMN IF NOT EXISTS file_hash VARCHAR(64)")
    print("file_hash column added")

    cur2.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_file_hash "
        "ON thought_entries(file_hash) WHERE file_hash IS NOT NULL"
    )
    print("unique index created")

    cur2.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='thought_entries' AND column_name='file_hash'"
    )
    print("column check:", cur2.fetchone())
    conn2.close()
except Exception as e:
    print(f"DDL error: {e}")

print("migration complete")
