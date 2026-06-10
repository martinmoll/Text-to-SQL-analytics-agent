"""
Ingest daily OHLCV price data from yfinance into DuckDB.

Pulls 5 years of history for a universe of ~30 tickers (US large-caps,
Oslo Bors stocks, and major indices), loads into a raw staging table,
then transforms into the canonical fact_daily_prices table with computed
daily returns. Also builds dim_securities and dim_calendar.
"""

import duckdb
import yfinance as yf
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

WAREHOUSE_PATH = Path(__file__).parent.parent / "warehouse.duckdb"

TICKERS = [
    # US large-caps
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
    "JPM", "V", "JNJ", "PG", "UNH", "HD", "MA", "PFE",
    # Oslo Bors
    "EQNR.OL", "DNB.OL", "MOWI.OL", "ORK.OL", "TEL.OL", "AKRBP.OL",
    # Indices
    "^GSPC", "^OEX", "^IXIC",
]

HISTORY_YEARS = 5


def create_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS raw_daily_prices (
            ticker VARCHAR NOT NULL,
            date DATE NOT NULL,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            adjusted_close DOUBLE,
            volume BIGINT,
            PRIMARY KEY (ticker, date)
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS fact_daily_prices (
            ticker VARCHAR NOT NULL,
            date DATE NOT NULL,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            adjusted_close DOUBLE,
            volume BIGINT,
            daily_simple_return DOUBLE,
            daily_log_return DOUBLE,
            PRIMARY KEY (ticker, date)
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS dim_securities (
            ticker VARCHAR PRIMARY KEY,
            short_name VARCHAR,
            long_name VARCHAR,
            security_type VARCHAR NOT NULL DEFAULT 'equity',
            sector VARCHAR,
            industry VARCHAR,
            exchange VARCHAR,
            currency VARCHAR,
            market_cap BIGINT,
            country VARCHAR
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS dim_calendar (
            date DATE NOT NULL,
            exchange VARCHAR NOT NULL,
            year INTEGER,
            quarter INTEGER,
            month INTEGER,
            day_of_week INTEGER,
            day_name VARCHAR,
            is_month_start BOOLEAN,
            is_month_end BOOLEAN,
            is_quarter_start BOOLEAN,
            is_quarter_end BOOLEAN,
            PRIMARY KEY (date, exchange)
        )
    """)


def fetch_prices(tickers: list[str], years: int) -> pd.DataFrame:
    end = datetime.today()
    start = end - timedelta(days=years * 365)

    print(f"Fetching {years}y of daily prices for {len(tickers)} tickers...")
    data = yf.download(
        tickers,
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        group_by="ticker",
        auto_adjust=False,
        threads=True,
    )

    rows = []
    for ticker in tickers:
        try:
            if len(tickers) == 1:
                ticker_data = data
            else:
                ticker_data = data[ticker]

            if ticker_data.empty:
                print(f"  WARNING: No data for {ticker}")
                continue

            for date_val, row in ticker_data.iterrows():
                rows.append({
                    "ticker": ticker,
                    "date": pd.Timestamp(date_val).date(),
                    "open": float(row["Open"]) if pd.notna(row["Open"]) else None,
                    "high": float(row["High"]) if pd.notna(row["High"]) else None,
                    "low": float(row["Low"]) if pd.notna(row["Low"]) else None,
                    "close": float(row["Close"]) if pd.notna(row["Close"]) else None,
                    "adjusted_close": float(row["Adj Close"]) if pd.notna(row["Adj Close"]) else None,
                    "volume": int(row["Volume"]) if pd.notna(row["Volume"]) else None,
                })
        except (KeyError, TypeError) as e:
            print(f"  WARNING: Could not process {ticker}: {e}")
            continue

    print(f"  Collected {len(rows)} price rows")
    return pd.DataFrame(rows)


def _classify_security_type(ticker: str) -> str:
    if ticker.startswith("^"):
        return "index"
    return "equity"


def _classify_exchange(ticker: str, info_exchange: str | None) -> str:
    if ticker.endswith(".OL"):
        return "OSE"
    if ticker.startswith("^"):
        return "US"
    if info_exchange:
        return info_exchange
    return "US"


def fetch_security_info(tickers: list[str]) -> pd.DataFrame:
    print(f"Fetching security metadata for {len(tickers)} tickers...")
    rows = []
    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).info
            raw_exchange = info.get("exchange")
            rows.append({
                "ticker": ticker,
                "short_name": info.get("shortName"),
                "long_name": info.get("longName"),
                "security_type": _classify_security_type(ticker),
                "sector": info.get("sector"),
                "industry": info.get("industry"),
                "exchange": _classify_exchange(ticker, raw_exchange),
                "currency": info.get("currency", "USD"),
                "market_cap": info.get("marketCap"),
                "country": info.get("country"),
            })
            print(f"  {ticker}: {info.get('shortName', 'unknown')} [{_classify_security_type(ticker)}]")
        except Exception as e:
            print(f"  WARNING: Could not fetch info for {ticker}: {e}")
            rows.append({
                "ticker": ticker,
                "short_name": None,
                "long_name": None,
                "security_type": _classify_security_type(ticker),
                "sector": None,
                "industry": None,
                "exchange": _classify_exchange(ticker, None),
                "currency": None,
                "market_cap": None,
                "country": None,
            })
    return pd.DataFrame(rows)


def load_raw_prices(con: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> None:
    con.execute("DELETE FROM raw_daily_prices")
    con.execute("""
        INSERT INTO raw_daily_prices
        SELECT * FROM df
    """)
    count = con.execute("SELECT COUNT(*) FROM raw_daily_prices").fetchone()[0]
    print(f"  Loaded {count} rows into raw_daily_prices")


def build_fact_daily_prices(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("DELETE FROM fact_daily_prices")
    con.execute("""
        INSERT INTO fact_daily_prices
        SELECT
            ticker,
            date,
            open,
            high,
            low,
            close,
            adjusted_close,
            volume,
            (adjusted_close - LAG(adjusted_close) OVER (PARTITION BY ticker ORDER BY date))
                / NULLIF(LAG(adjusted_close) OVER (PARTITION BY ticker ORDER BY date), 0)
                AS daily_simple_return,
            LN(adjusted_close / NULLIF(LAG(adjusted_close) OVER (PARTITION BY ticker ORDER BY date), 0))
                AS daily_log_return
        FROM raw_daily_prices
        ORDER BY ticker, date
    """)
    count = con.execute("SELECT COUNT(*) FROM fact_daily_prices").fetchone()[0]
    print(f"  Built fact_daily_prices with {count} rows")


def load_dim_securities(con: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> None:
    con.execute("DELETE FROM dim_securities")
    con.execute("""
        INSERT INTO dim_securities
        SELECT * FROM df
    """)
    count = con.execute("SELECT COUNT(*) FROM dim_securities").fetchone()[0]
    print(f"  Loaded {count} rows into dim_securities")


def build_dim_calendar(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("DELETE FROM dim_calendar")
    con.execute("""
        INSERT INTO dim_calendar
        SELECT DISTINCT
            r.date,
            s.exchange,
            YEAR(r.date) AS year,
            QUARTER(r.date) AS quarter,
            MONTH(r.date) AS month,
            DAYOFWEEK(r.date) AS day_of_week,
            DAYNAME(r.date) AS day_name,
            r.date = DATE_TRUNC('month', r.date) AS is_month_start,
            r.date = LAST_DAY(r.date) AS is_month_end,
            r.date = DATE_TRUNC('quarter', r.date) AS is_quarter_start,
            MONTH(r.date) IN (3, 6, 9, 12) AND r.date = LAST_DAY(r.date) AS is_quarter_end
        FROM raw_daily_prices r
        JOIN dim_securities s ON r.ticker = s.ticker
        WHERE s.security_type = 'equity'
        ORDER BY r.date, s.exchange
    """)
    count = con.execute("SELECT COUNT(*) FROM dim_calendar").fetchone()[0]
    exchanges = con.execute("SELECT DISTINCT exchange FROM dim_calendar ORDER BY exchange").fetchall()
    exchange_list = [e[0] for e in exchanges]
    print(f"  Built dim_calendar with {count} rows across exchanges: {', '.join(exchange_list)}")


def main() -> None:
    print(f"=== Price Ingestion Pipeline ===")
    print(f"Warehouse: {WAREHOUSE_PATH}")
    print()

    con = duckdb.connect(str(WAREHOUSE_PATH))

    print("Step 1: Creating tables...")
    create_tables(con)
    print()

    print("Step 2: Fetching price data from yfinance...")
    prices_df = fetch_prices(TICKERS, HISTORY_YEARS)
    print()

    print("Step 3: Loading raw prices into DuckDB...")
    load_raw_prices(con, prices_df)
    print()

    print("Step 4: Building fact_daily_prices with computed returns...")
    build_fact_daily_prices(con)
    print()

    print("Step 5: Fetching security metadata...")
    securities_df = fetch_security_info(TICKERS)
    print()

    print("Step 6: Loading dim_securities...")
    load_dim_securities(con, securities_df)
    print()

    print("Step 7: Building dim_calendar (exchange-aware)...")
    build_dim_calendar(con)
    print()

    # Summary
    print("=== Ingestion Complete ===")
    for table in ["raw_daily_prices", "fact_daily_prices", "dim_securities", "dim_calendar"]:
        count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {count} rows")

    # Show date range
    result = con.execute("""
        SELECT MIN(date), MAX(date), COUNT(DISTINCT ticker)
        FROM fact_daily_prices
    """).fetchone()
    print(f"\n  Date range: {result[0]} to {result[1]}")
    print(f"  Tickers: {result[2]}")

    con.close()


if __name__ == "__main__":
    main()
