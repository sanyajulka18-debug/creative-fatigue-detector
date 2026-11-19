"""
build_dataset.py
----------------
Runs the SQL pipeline:
    raw CSV  ->  SQLite  ->  sql/01_clean.sql  ->  sql/02_features.sql  ->  processed CSVs

Also writes a cleaning log that shows how many rows each cleaning rule removed.

Usage:
    python src/build_dataset.py                      # uses data/raw/ad_daily_raw.csv
    python src/build_dataset.py path/to/export.csv   # use your own Ads Manager export
"""

import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW_CSV = ROOT / "data" / "raw" / "ad_daily_raw.csv"
DB_PATH = ROOT / "data" / "processed" / "ads.db"
OUT_DIR = ROOT / "data" / "processed"

REQUIRED_COLUMNS = [
    "date", "ad_id", "campaign_id", "format", "hook_type", "launch_date",
    "spend_inr", "impressions", "reach", "clicks", "purchases",
]


def run_sql_file(conn: sqlite3.Connection, path: Path) -> None:
    conn.executescript(path.read_text())
    conn.commit()


def count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def main(raw_path: Path) -> None:
    raw = pd.read_csv(raw_path)
    missing = [c for c in REQUIRED_COLUMNS if c not in raw.columns]
    if missing:
        raise ValueError(f"Raw file is missing columns: {missing}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.unlink(missing_ok=True)
    conn = sqlite3.connect(DB_PATH)
    raw[REQUIRED_COLUMNS].to_sql("raw_ad_daily", conn, index=False)

    # run every .sql file in order: 01_clean.sql, then 02_features.sql (once it exists)
    for sql_file in sorted((ROOT / "sql").glob("*.sql")):
        print(f"Running {sql_file.name}")
        run_sql_file(conn, sql_file)

    # ---- cleaning log ----
    steps = [
        ("Raw rows", "raw_ad_daily"),
        ("After removing duplicate rows", "s1_dedup"),
        ("After removing zero-impression days", "s2_delivered"),
        ("After removing ads with < 3 days", "s3_min_history"),
    ]
    log, prev = [], None
    for label, table in steps:
        n = count(conn, table)
        log.append({"step": label, "rows": n, "removed": 0 if prev is None else prev - n})
        prev = n
    log_df = pd.DataFrame(log)
    ads_before = conn.execute("SELECT COUNT(DISTINCT ad_id) FROM raw_ad_daily").fetchone()[0]
    ads_after = conn.execute("SELECT COUNT(DISTINCT ad_id) FROM ad_daily_clean").fetchone()[0]
    log_df.to_csv(OUT_DIR / "cleaning_log.csv", index=False)

    print(log_df.to_string(index=False))
    print(f"\nAds: {ads_before} raw -> {ads_after} after cleaning")

    # ---- export every output table to CSV ----
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    for table in ["ad_daily_clean", "ad_daily_features", "ad_summary"]:
        if table in tables:
            df = pd.read_sql(f"SELECT * FROM {table}", conn)
            df.to_csv(OUT_DIR / f"{table}.csv", index=False)
            print(f"Saved {table}.csv ({len(df):,} rows)")
    conn.close()


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else RAW_CSV)
