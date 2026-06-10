# Market Overview — Reference Document

## Business Context

Market overview queries ask about broad market trends, sector comparisons, index performance, and cross-exchange analysis. These questions require aggregating across multiple tickers and often involve ranking or filtering by performance.

## Key Tables

### fact_daily_prices
Used for all price-based performance metrics. Remember that indices (^GSPC, ^OEX, ^IXIC) are in this table alongside equities.

### dim_securities
Essential for market overview queries — provides `sector`, `exchange`, `security_type`, and `currency` for grouping and filtering.

| security_type | Tickers | Description |
|---|---|---|
| equity | AAPL, MSFT, ..., EQNR.OL, ... | Individual stocks |
| index | ^GSPC, ^OEX, ^IXIC | Market indices |

### dim_calendar
Exchange-aware trading calendar. Grain: `(date, exchange)`. Use for counting trading days per exchange.

## Available Indices

| Ticker | Name | Description |
|---|---|---|
| ^GSPC | S&P 500 | Broad US large-cap index (500 stocks) |
| ^OEX | S&P 100 | Largest 100 US stocks, subset of S&P 500 |
| ^IXIC | NASDAQ Composite | All Nasdaq-listed stocks |

**Note:** We do NOT have OSEBX (Oslo Bors benchmark index) or DJIA in the warehouse.

## Available Sectors

The following sectors are populated in `dim_securities.sector` (sourced from yfinance). Only equities have sectors — indices have NULL.

Sectors present: Technology, Financial Services, Healthcare, Consumer Cyclical, Consumer Defensive, Energy, Communication Services, Industrials (varies by current ticker universe).

## Gotchas

### 1. Indices vs equities
When the user asks "which stocks performed best," ALWAYS filter `security_type = 'equity'`. Indices should only be included when explicitly requested or when used as a benchmark comparison.

### 2. Sector NULL values
Indices have `sector = NULL`. Oslo Bors stocks may have different sector naming from US stocks (both use yfinance's classification, but coverage can vary). Always use `WHERE sector IS NOT NULL` when grouping by sector.

### 3. Cross-exchange comparisons
When comparing US vs Oslo Bors performance:
- Returns (ratios) are comparable but currency-dependent — note this
- Price levels are NOT comparable (different currencies, different scales)
- Trading days differ — use each exchange's own calendar for "trading days in period" counts
- Volume is not comparable across exchanges

### 4. Survivorship bias
Our ticker universe is fixed at ingestion time. If a stock was delisted or removed from an index during the 5-year window, it may not be in our data. This creates survivorship bias in "best performer" rankings.

### 5. Equal-weight vs cap-weight
When computing "sector average return," a simple AVG gives equal weight to each stock. This is NOT how sector indices work (they're typically cap-weighted). Always state that your sector averages are equal-weighted across the stocks in our universe.

### 6. "Market" is ambiguous
"How is the market doing?" could mean the S&P 500, Nasdaq, or a custom basket. Default to ^GSPC unless context suggests otherwise. For Norwegian context, note we don't have OSEBX.

## Common Query Patterns

### Index performance over a period
```sql
SELECT
    fp.ticker,
    s.short_name,
    (LAST(fp.adjusted_close ORDER BY fp.date) / FIRST(fp.adjusted_close ORDER BY fp.date)) - 1 AS period_return
FROM fact_daily_prices fp
JOIN dim_securities s ON fp.ticker = s.ticker
WHERE s.security_type = 'index'
  AND fp.date BETWEEN '2024-01-01' AND '2024-12-31'
  AND fp.adjusted_close IS NOT NULL
GROUP BY fp.ticker, s.short_name
ORDER BY period_return DESC
```

### Best/worst performing stocks
```sql
SELECT
    fp.ticker,
    s.short_name,
    s.sector,
    s.exchange,
    (LAST(fp.adjusted_close ORDER BY fp.date) / FIRST(fp.adjusted_close ORDER BY fp.date)) - 1 AS period_return
FROM fact_daily_prices fp
JOIN dim_securities s ON fp.ticker = s.ticker
WHERE s.security_type = 'equity'
  AND fp.date BETWEEN '2024-01-01' AND '2024-12-31'
  AND fp.adjusted_close IS NOT NULL
GROUP BY fp.ticker, s.short_name, s.sector, s.exchange
ORDER BY period_return DESC
```

### Sector performance (equal-weight average)
```sql
WITH stock_returns AS (
    SELECT
        fp.ticker,
        s.sector,
        (LAST(fp.adjusted_close ORDER BY fp.date) / FIRST(fp.adjusted_close ORDER BY fp.date)) - 1 AS period_return
    FROM fact_daily_prices fp
    JOIN dim_securities s ON fp.ticker = s.ticker
    WHERE s.security_type = 'equity'
      AND s.sector IS NOT NULL
      AND fp.date BETWEEN '2024-01-01' AND '2024-12-31'
      AND fp.adjusted_close IS NOT NULL
    GROUP BY fp.ticker, s.sector
)
SELECT
    sector,
    COUNT(*) AS num_stocks,
    AVG(period_return) AS avg_return,
    MIN(period_return) AS worst_return,
    MAX(period_return) AS best_return
FROM stock_returns
GROUP BY sector
ORDER BY avg_return DESC
```

### US vs Oslo Bors performance comparison
```sql
WITH stock_metrics AS (
    SELECT
        fp.ticker,
        s.exchange,
        s.currency,
        (LAST(fp.adjusted_close ORDER BY fp.date) / FIRST(fp.adjusted_close ORDER BY fp.date)) - 1 AS period_return,
        STDDEV(fp.daily_log_return) * SQRT(252) AS ann_volatility
    FROM fact_daily_prices fp
    JOIN dim_securities s ON fp.ticker = s.ticker
    WHERE s.security_type = 'equity'
      AND fp.date BETWEEN '2024-01-01' AND '2024-12-31'
      AND fp.adjusted_close IS NOT NULL
    GROUP BY fp.ticker, s.exchange, s.currency
)
SELECT
    CASE
        WHEN exchange IN ('NMS', 'NYQ') THEN 'US'
        WHEN exchange = 'OSE' THEN 'Oslo Bors'
    END AS market,
    currency,
    COUNT(*) AS num_stocks,
    AVG(period_return) AS avg_return,
    AVG(ann_volatility) AS avg_volatility
FROM stock_metrics
GROUP BY market, currency
```
Note: Always include currency in the output and note that returns are in local currency.

### Market breadth (% of stocks with positive returns)
```sql
WITH stock_returns AS (
    SELECT
        fp.ticker,
        (LAST(fp.adjusted_close ORDER BY fp.date) / FIRST(fp.adjusted_close ORDER BY fp.date)) - 1 AS period_return
    FROM fact_daily_prices fp
    JOIN dim_securities s ON fp.ticker = s.ticker
    WHERE s.security_type = 'equity'
      AND fp.date BETWEEN '2024-01-01' AND '2024-12-31'
      AND fp.adjusted_close IS NOT NULL
    GROUP BY fp.ticker
)
SELECT
    COUNT(*) AS total_stocks,
    COUNT(*) FILTER (WHERE period_return > 0) AS positive,
    COUNT(*) FILTER (WHERE period_return <= 0) AS negative,
    ROUND(100.0 * COUNT(*) FILTER (WHERE period_return > 0) / COUNT(*), 1) AS pct_positive
FROM stock_returns
```

### Monthly return heatmap data (all tickers)
```sql
SELECT
    fp.ticker,
    DATE_TRUNC('month', fp.date) AS month,
    EXP(SUM(fp.daily_log_return)) - 1 AS monthly_return
FROM fact_daily_prices fp
JOIN dim_securities s ON fp.ticker = s.ticker
WHERE s.security_type = 'equity'
  AND fp.date >= '2024-01-01'
GROUP BY fp.ticker, DATE_TRUNC('month', fp.date)
ORDER BY fp.ticker, month
```

### Volatility ranking (most to least volatile)
```sql
SELECT
    fp.ticker,
    s.short_name,
    s.sector,
    STDDEV(fp.daily_log_return) * SQRT(252) AS ann_volatility,
    COUNT(*) AS trading_days
FROM fact_daily_prices fp
JOIN dim_securities s ON fp.ticker = s.ticker
WHERE s.security_type = 'equity'
  AND fp.date BETWEEN '2024-01-01' AND '2024-12-31'
GROUP BY fp.ticker, s.short_name, s.sector
ORDER BY ann_volatility DESC
```
