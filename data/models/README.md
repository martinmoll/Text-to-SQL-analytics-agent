# Data Models

SQL definitions for the dimensional model in DuckDB.

- `dim_securities.sql` — Ticker → name, sector, exchange, currency
- `dim_calendar.sql` — Trading days, quarters, fiscal periods
- `fact_daily_prices.sql` — OHLCV, adjusted close, daily returns (grain: ticker × date)
- `fact_fundamentals.sql` — Quarterly financials (grain: ticker × fiscal_quarter)
- `agg_monthly_returns.sql` — Pre-aggregated monthly return series
