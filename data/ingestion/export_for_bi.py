"""
Export warehouse tables to CSV for BI tools (Power BI, Tableau, Excel).

Writes one CSV per table into data/exports/ (gitignored). Power BI:
Get Data -> Text/CSV, or point the Folder connector at data/exports/.
"""

import duckdb
from pathlib import Path

WAREHOUSE_PATH = Path(__file__).parent.parent / "warehouse.duckdb"
EXPORT_DIR = Path(__file__).parent.parent / "exports"

TABLES = [
    "fact_daily_prices",
    "dim_securities",
    "dim_calendar",
    "raw_daily_prices",
]


def main() -> None:
    if not WAREHOUSE_PATH.exists():
        print("ERROR: Warehouse does not exist. Run ingest_prices.py first.")
        return

    EXPORT_DIR.mkdir(exist_ok=True)
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)

    print(f"=== BI Export ===")
    print(f"Warehouse: {WAREHOUSE_PATH}")
    print(f"Export dir: {EXPORT_DIR}\n")

    for table in TABLES:
        out_path = EXPORT_DIR / f"{table}.csv"
        con.execute(f"COPY (SELECT * FROM {table}) TO '{out_path}' (HEADER, DELIMITER ',')")
        rows = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        size_kb = out_path.stat().st_size / 1024
        print(f"  {table}: {rows:,} rows -> {out_path.name} ({size_kb:,.0f} KB)")

    con.close()
    print("\nDone. In Power BI: Get Data -> Text/CSV (one per file).")


if __name__ == "__main__":
    main()
