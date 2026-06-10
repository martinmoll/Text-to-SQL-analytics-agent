"""
Data quality checks for the DuckDB warehouse.

Checks:
- Freshness: max date in fact_daily_prices within 5 trading days of today
- Completeness: flag any ticker with >5% missing trading days
- Anomalies: flag any row where abs(daily_simple_return) > 0.20
"""

import duckdb
from pathlib import Path
from datetime import date

WAREHOUSE_PATH = Path(__file__).parent.parent / "warehouse.duckdb"


def check_freshness(con: duckdb.DuckDBPyConnection) -> bool:
    result = con.execute("SELECT MAX(date) FROM fact_daily_prices").fetchone()
    max_date = result[0]

    if max_date is None:
        print("  FAIL: No data in fact_daily_prices")
        return False

    today = date.today()
    days_stale = (today - max_date).days

    # 5 trading days ≈ 7 calendar days (account for weekends)
    threshold_calendar_days = 9
    passed = days_stale <= threshold_calendar_days

    status = "PASS" if passed else "FAIL"
    print(f"  {status}: Most recent date is {max_date} ({days_stale} calendar days ago)")
    if not passed:
        print(f"         Expected within {threshold_calendar_days} calendar days (~5 trading days)")

    return passed


def check_completeness(con: duckdb.DuckDBPyConnection) -> bool:
    results = con.execute("""
        WITH date_range AS (
            SELECT
                fp.ticker,
                s.exchange,
                COUNT(DISTINCT fp.date) AS actual_days,
                MIN(fp.date) AS first_date,
                MAX(fp.date) AS last_date
            FROM fact_daily_prices fp
            JOIN dim_securities s ON fp.ticker = s.ticker
            GROUP BY fp.ticker, s.exchange
        ),
        expected AS (
            SELECT
                dr.ticker,
                dr.actual_days,
                COUNT(DISTINCT dc.date) AS expected_days,
                dr.first_date,
                dr.last_date
            FROM date_range dr
            JOIN dim_calendar dc
                ON dc.exchange = dr.exchange
                AND dc.date BETWEEN dr.first_date AND dr.last_date
            GROUP BY dr.ticker, dr.actual_days, dr.first_date, dr.last_date
        )
        SELECT
            ticker,
            actual_days,
            expected_days,
            ROUND(100.0 * (expected_days - actual_days) / expected_days, 2) AS pct_missing
        FROM expected
        WHERE expected_days > 0
        ORDER BY pct_missing DESC
    """).fetchall()

    all_passed = True
    flagged = []

    for ticker, actual, expected, pct_missing in results:
        if pct_missing > 5.0:
            flagged.append((ticker, actual, expected, pct_missing))
            all_passed = False

    if flagged:
        print(f"  WARN: {len(flagged)} ticker(s) with >5% missing trading days:")
        for ticker, actual, expected, pct_missing in flagged:
            print(f"         {ticker}: {actual}/{expected} days ({pct_missing}% missing)")
    else:
        print(f"  PASS: All {len(results)} tickers have <5% missing trading days")

    return all_passed


def check_anomalies(con: duckdb.DuckDBPyConnection) -> bool:
    results = con.execute("""
        SELECT
            ticker,
            date,
            daily_simple_return,
            adjusted_close
        FROM fact_daily_prices
        WHERE ABS(daily_simple_return) > 0.20
        ORDER BY ABS(daily_simple_return) DESC
    """).fetchall()

    if results:
        print(f"  WARN: {len(results)} row(s) with |daily_simple_return| > 20%:")
        for ticker, dt, ret, price in results[:10]:
            print(f"         {ticker} on {dt}: {ret:+.4f} (price: {price:.2f})")
        if len(results) > 10:
            print(f"         ... and {len(results) - 10} more")
        return False
    else:
        print(f"  PASS: No extreme daily returns (>20%) detected")
        return True


def check_row_counts(con: duckdb.DuckDBPyConnection) -> None:
    for table in ["raw_daily_prices", "fact_daily_prices", "dim_securities", "dim_calendar"]:
        count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {count:,} rows")


def main() -> None:
    print(f"=== Data Quality Report ===")
    print(f"Warehouse: {WAREHOUSE_PATH}")
    print(f"Run date: {date.today()}")
    print()

    if not WAREHOUSE_PATH.exists():
        print("ERROR: Warehouse does not exist. Run ingest_prices.py first.")
        return

    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)

    print("Row counts:")
    check_row_counts(con)
    print()

    print("Freshness check:")
    freshness_ok = check_freshness(con)
    print()

    print("Completeness check:")
    completeness_ok = check_completeness(con)
    print()

    print("Anomaly check:")
    anomalies_ok = check_anomalies(con)
    print()

    # Summary
    checks = {
        "Freshness": freshness_ok,
        "Completeness": completeness_ok,
        "Anomalies": anomalies_ok,
    }

    print("=== Summary ===")
    all_passed = True
    for name, passed in checks.items():
        status = "PASS" if passed else "WARN"
        if not passed:
            all_passed = False
        print(f"  [{status}] {name}")

    print()
    if all_passed:
        print("All checks passed.")
    else:
        print("Some checks have warnings — review above for details.")

    con.close()


if __name__ == "__main__":
    main()
