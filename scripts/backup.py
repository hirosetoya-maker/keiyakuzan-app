"""
Supabaseの全テーブルをCSVへバックアップするスクリプト。
GitHub Actionsから毎日実行される（.github/workflows/backup.yml）。

このスクリプト自体がSupabaseへ定期的にアクセスするため、
無料プランの「7日間無アクセスで自動一時停止」も同時に防止できる。
"""
import csv
import os
import sys

from supabase import create_client

TABLES = ["contracts", "deliveries", "import_batches", "jv_options"]
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "backups", "latest")


def fetch_all(supabase, table: str) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        res = supabase.table(table).select("*").range(offset, offset + 999).execute()
        rows.extend(res.data)
        if len(res.data) < 1000:
            break
        offset += 1000
    return rows


def write_csv(path: str, rows: list[dict]) -> None:
    if not rows:
        # 空でもファイル自体は残す（列不明なので空ファイルのみ）
        open(path, "w", encoding="utf-8-sig").close()
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("SUPABASE_URL / SUPABASE_KEY が設定されていません", file=sys.stderr)
        sys.exit(1)

    supabase = create_client(url, key)
    os.makedirs(OUT_DIR, exist_ok=True)

    total = 0
    for table in TABLES:
        rows = fetch_all(supabase, table)
        write_csv(os.path.join(OUT_DIR, f"{table}.csv"), rows)
        print(f"{table}: {len(rows)}件")
        total += len(rows)

    print(f"バックアップ完了（合計{total}件）")


if __name__ == "__main__":
    main()
