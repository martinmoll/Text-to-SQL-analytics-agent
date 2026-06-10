# Returns & Risk — Reference Document

## Business Context

Return and risk metrics are the foundation of equity analysis. This reference covers how to correctly compute, aggregate, and compare returns and risk measures using the warehouse's daily price data.

**Key distinction:** We store two return types as pre-computed columns:
- **Simple return** — `(P_t - P_{t-1}) / P_{t-1}` — intuitive, used for single-period performance
- **Log return** — `ln(P_t / P_{t-1})` — additive across time, used for multi-period aggregation and volatility

**Rule of thumb:** Use simple returns when reporting a single period's performance to a user. Use log returns for statistical calculations (volatility, Sharpe, regression).

## Key Tables

### fact_daily_prices
| Column | Type | Description |
|---|---|---|
| ticker | VARCHAR | Security identifier (PK with date) |
| date | DATE | Trading date (PK with ticker) |
| open | DOUBLE | Opening price |
| high | DOUBLE | Intraday high |
| low | DOUBLE | Intraday low |
| close | DOUBLE | Closing price (unadjusted) |
| adjusted_close | DOUBLE | Split/dividend adjusted closing price |
| volume | BIGINT | Daily trading volume |
| daily_simple_return | DOUBLE | Pre-computed: `(adj_close - prev_adj_close) / prev_adj_close` |
| daily_log_return | DOUBLE | Pre-computed: `ln(adj_close / prev_adj_close)` |

**Grain:** One row per ticker per trading day.
**Important:** `daily_simple_return` and `daily_log_return` are NULL for the first trading day per ticker (no prior price to compare against).

### dim_securities (for filtering)
Join on `fp.ticker = s.ticker` to access `security_type`, `exchange`, `currency`.

## Gotchas

### 1. Adjusted close vs close
**ALWAYS use `adjusted_close` for return calculations.** The `close` column does not account for stock splits or dividends. Using `close` will produce wildly wrong returns on split dates.

### 2. NULL first rows
The first row per ticker has NULL returns. When computing averages or standard deviations, these NULLs are automatically excluded by SQL aggregate functions, which is correct. But be aware when counting observations.

### 3. Multi-period returns
- **Simple returns do NOT compound by addition.** To get a cumulative simple return: `(1 + r1) * (1 + r2) * ... - 1`, or equivalently: `last_price / first_price - 1`.
- **Log returns DO add.** Cumulative log return = `SUM(daily_log_return)`. Convert back to simple: `EXP(SUM(log_return)) - 1`.

### 4. Annualization convention
- Trading days per year: **252** (standard for equities)
- Annualized return: `AVG(daily_log_return) * 252`
- Annualized volatility: `STDDEV(daily_log_return) * SQRT(252)`
- Do NOT annualize over very short windows (<20 days) — the result will be meaningless noise.

### 5. Currency
Returns are currency-denominated. A 10% return in NOK is not the same as 10% in USD unless FX rates are flat. When comparing returns across exchanges, always note the currency in the response.

### 6. NULL prices on partial trading days
Some tickers (especially Oslo Bors) may have NULL `adjusted_close` for the most recent date when data hasn't fully settled. When using `LAST()` or `FIRST()` ordered aggregates, **always filter `AND fp.adjusted_close IS NOT NULL`** — otherwise LAST picks up the NULL and the entire calculation returns NULL/NaN.

### 7. Sharpe ratio risk-free rate
The default risk-free rate in the semantic layer is 5% annualized. If the user doesn't specify, use the default but mention it. The user may want to use the current 3-month US Treasury yield instead.

## Common Query Patterns

### Cumulative return over a date range
```sql
SELECT
    fp.ticker,
    (LAST(fp.adjusted_close ORDER BY fp.date) / FIRST(fp.adjusted_close ORDER BY fp.date)) - 1 AS cumulative_return
FROM fact_daily_prices fp
WHERE fp.ticker = 'AAPL'
  AND fp.date BETWEEN '2024-01-01' AND '2024-12-31'
  AND fp.adjusted_close IS NOT NULL
GROUP BY fp.ticker
```

### Annualized volatility
```sql
SELECT
    fp.ticker,
    STDDEV(fp.daily_log_return) * SQRT(252) AS ann_volatility
FROM fact_daily_prices fp
WHERE fp.ticker IN ('AAPL', 'MSFT')
  AND fp.date BETWEEN '2024-01-01' AND '2024-12-31'
GROUP BY fp.ticker
```

### Sharpe ratio (ex-post)
```sql
SELECT
    fp.ticker,
    (AVG(fp.daily_log_return) * 252 - 0.05) / (STDDEV(fp.daily_log_return) * SQRT(252)) AS sharpe_ratio
FROM fact_daily_prices fp
WHERE fp.date BETWEEN '2024-01-01' AND '2024-12-31'
GROUP BY fp.ticker
```

### Rolling 60-day volatility
```sql
SELECT
    fp.ticker,
    fp.date,
    STDDEV(fp.daily_log_return) OVER (
        PARTITION BY fp.ticker ORDER BY fp.date
        ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
    ) * SQRT(252) AS rolling_vol_60d
FROM fact_daily_prices fp
WHERE fp.ticker = 'AAPL'
ORDER BY fp.date
```

### Maximum drawdown
```sql
WITH running_max AS (
    SELECT
        fp.ticker,
        fp.date,
        fp.adjusted_close,
        MAX(fp.adjusted_close) OVER (
            PARTITION BY fp.ticker ORDER BY fp.date ROWS UNBOUNDED PRECEDING
        ) AS peak
    FROM fact_daily_prices fp
    WHERE fp.ticker = 'TSLA'
      AND fp.date BETWEEN '2024-01-01' AND '2024-12-31'
)
SELECT
    ticker,
    MIN(adjusted_close / peak - 1) AS max_drawdown
FROM running_max
GROUP BY ticker
```

### Sortino ratio
```sql
SELECT
    fp.ticker,
    (AVG(fp.daily_log_return) * 252 - 0.05)
    / (SQRT(AVG(CASE WHEN fp.daily_log_return < 0 THEN fp.daily_log_return * fp.daily_log_return ELSE 0 END)) * SQRT(252))
    AS sortino_ratio
FROM fact_daily_prices fp
WHERE fp.date BETWEEN '2024-01-01' AND '2024-12-31'
GROUP BY fp.ticker
```

### Monthly return series
```sql
SELECT
    fp.ticker,
    DATE_TRUNC('month', fp.date) AS month,
    EXP(SUM(fp.daily_log_return)) - 1 AS monthly_return
FROM fact_daily_prices fp
WHERE fp.ticker = 'AAPL'
GROUP BY fp.ticker, DATE_TRUNC('month', fp.date)
ORDER BY month
```
