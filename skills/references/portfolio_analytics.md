# Portfolio Analytics — Reference Document

## Business Context

Portfolio analytics measures how securities relate to each other and to market benchmarks. This covers correlation analysis, beta estimation, diversification assessment, and relative performance. These are critical for portfolio construction and risk management.

## Key Tables

### fact_daily_prices (self-joined for pairwise analysis)
Most portfolio analytics require comparing two tickers on matching dates. This means self-joining `fact_daily_prices`:

```sql
-- Asset vs benchmark pattern
FROM fact_daily_prices asset
JOIN fact_daily_prices market
    ON asset.date = market.date
    AND market.ticker = '^GSPC'
WHERE asset.ticker = 'AAPL'
```

### dim_securities (for grouping by sector/exchange)
Used to slice portfolio analysis by sector, exchange, or security type.

## Gotchas

### 1. Matching dates across exchanges
US and Oslo Bors have different trading calendars. When correlating a US stock with a Norwegian stock, only dates where BOTH traded should be included. The inner join on `date` handles this automatically, but be aware that fewer observations mean less statistical power.

```sql
-- This correctly uses only overlapping trading days
FROM fact_daily_prices a
JOIN fact_daily_prices b ON a.date = b.date
WHERE a.ticker = 'AAPL' AND b.ticker = 'EQNR.OL'
```

### 2. Beta benchmark selection
- Default benchmark: **^GSPC** (S&P 500) for US stocks
- For Oslo Bors stocks, ^GSPC is still a reasonable benchmark for global market risk, but the user may want a local benchmark (OSEBX) which is NOT in our data
- Always state which benchmark was used in the response

### 3. Observation count matters
Regression-based metrics (beta, R²) need sufficient data points. Flag results with fewer than 60 observations as low-confidence. Minimum recommended: 120 trading days (~6 months).

### 4. Stationarity assumption
Beta and correlation are not constant over time. A stock's beta can shift dramatically (e.g., TSLA's beta changed significantly as it entered the S&P 500). Consider showing rolling estimates alongside point estimates.

### 5. Correlation ≠ causation
Two stocks may be correlated because they're in the same sector, or because they both react to macro factors. Always note this when presenting correlation results.

### 6. Currency in cross-exchange analysis
Correlation of returns is currency-dependent. AAPL returns in USD and EQNR.OL returns in NOK include both the asset's performance and the implicit FX exposure. Note this when comparing.

## Common Query Patterns

### CAPM Beta (single stock vs benchmark)
```sql
SELECT
    asset.ticker,
    REGR_SLOPE(asset.daily_log_return, market.daily_log_return) AS beta,
    REGR_R2(asset.daily_log_return, market.daily_log_return) AS r_squared,
    REGR_INTERCEPT(asset.daily_log_return, market.daily_log_return) AS alpha_daily,
    COUNT(*) AS observations
FROM fact_daily_prices asset
JOIN fact_daily_prices market
    ON asset.date = market.date
    AND market.ticker = '^GSPC'
WHERE asset.ticker = 'AAPL'
  AND asset.date BETWEEN '2024-01-01' AND '2024-12-31'
GROUP BY asset.ticker
```

### Beta for all equities
```sql
SELECT
    asset.ticker,
    REGR_SLOPE(asset.daily_log_return, market.daily_log_return) AS beta,
    REGR_R2(asset.daily_log_return, market.daily_log_return) AS r_squared,
    COUNT(*) AS observations
FROM fact_daily_prices asset
JOIN fact_daily_prices market
    ON asset.date = market.date
    AND market.ticker = '^GSPC'
JOIN dim_securities s ON asset.ticker = s.ticker
WHERE s.security_type = 'equity'
  AND asset.date BETWEEN '2024-01-01' AND '2024-12-31'
GROUP BY asset.ticker
ORDER BY beta DESC
```

### Pairwise correlation
```sql
SELECT
    a.ticker AS ticker_1,
    b.ticker AS ticker_2,
    CORR(a.daily_log_return, b.daily_log_return) AS correlation,
    COUNT(*) AS observations
FROM fact_daily_prices a
JOIN fact_daily_prices b ON a.date = b.date
WHERE a.ticker = 'AAPL'
  AND b.ticker = 'MSFT'
  AND a.date BETWEEN '2024-01-01' AND '2024-12-31'
GROUP BY a.ticker, b.ticker
```

### Correlation matrix (all pairs in a set of tickers)
```sql
SELECT
    a.ticker AS ticker_1,
    b.ticker AS ticker_2,
    CORR(a.daily_log_return, b.daily_log_return) AS correlation
FROM fact_daily_prices a
JOIN fact_daily_prices b
    ON a.date = b.date
    AND a.ticker < b.ticker  -- avoid duplicates and self-pairs
WHERE a.ticker IN ('AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA')
  AND b.ticker IN ('AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA')
  AND a.date BETWEEN '2024-01-01' AND '2024-12-31'
GROUP BY a.ticker, b.ticker
ORDER BY a.ticker, b.ticker
```

### Rolling 60-day beta
```sql
WITH daily_pairs AS (
    SELECT
        asset.ticker,
        asset.date,
        asset.daily_log_return AS asset_return,
        market.daily_log_return AS market_return
    FROM fact_daily_prices asset
    JOIN fact_daily_prices market
        ON asset.date = market.date
        AND market.ticker = '^GSPC'
    WHERE asset.ticker = 'TSLA'
)
SELECT
    ticker,
    date,
    REGR_SLOPE(asset_return, market_return) OVER (
        PARTITION BY ticker ORDER BY date
        ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
    ) AS rolling_beta_60d
FROM daily_pairs
ORDER BY date
```

### Relative performance vs benchmark
```sql
WITH returns AS (
    SELECT
        asset.ticker,
        (LAST(asset.adjusted_close ORDER BY asset.date) / FIRST(asset.adjusted_close ORDER BY asset.date)) - 1 AS asset_return,
        (LAST(market.adjusted_close ORDER BY market.date) / FIRST(market.adjusted_close ORDER BY market.date)) - 1 AS benchmark_return
    FROM fact_daily_prices asset
    JOIN fact_daily_prices market
        ON asset.date = market.date
        AND market.ticker = '^GSPC'
    WHERE asset.date BETWEEN '2024-01-01' AND '2024-12-31'
      AND asset.adjusted_close IS NOT NULL
      AND market.adjusted_close IS NOT NULL
    GROUP BY asset.ticker
)
SELECT
    ticker,
    asset_return,
    benchmark_return,
    asset_return - benchmark_return AS excess_return
FROM returns
ORDER BY excess_return DESC
```

### Sector beta (average beta by sector)
```sql
SELECT
    s.sector,
    AVG(REGR_SLOPE(asset.daily_log_return, market.daily_log_return)) AS avg_sector_beta,
    COUNT(DISTINCT asset.ticker) AS num_stocks
FROM fact_daily_prices asset
JOIN fact_daily_prices market
    ON asset.date = market.date
    AND market.ticker = '^GSPC'
JOIN dim_securities s ON asset.ticker = s.ticker
WHERE s.security_type = 'equity'
  AND s.sector IS NOT NULL
  AND asset.date BETWEEN '2024-01-01' AND '2024-12-31'
GROUP BY s.sector
ORDER BY avg_sector_beta DESC
```
