# Data Ingestion

Scripts that pull financial data from external sources (yfinance) and load it into the DuckDB warehouse.

- `ingest_prices.py` — Daily OHLCV price data for all tickers
- `ingest_fundamentals.py` — Quarterly income statement and balance sheet data
- `quality_checks.py` — Freshness, completeness, and anomaly detection checks
