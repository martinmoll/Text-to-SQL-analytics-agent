# Analyst Skill — Procedural Playbook

Follow this workflow for every analytics question. Do not skip steps.

## Workflow

### Step 1: CLARIFY — Disambiguate the question

Before writing any SQL, resolve ambiguity in the user's question. Common ambiguities:

| Ambiguous Term | Clarification Needed |
|---|---|
| "returns" | Simple or log? Daily, monthly, cumulative, annualized? Total or excess (over risk-free)? |
| "volatility" | Annualized? What window — trailing 30d, 90d, 252d, or specific date range? |
| "Sharpe ratio" | Ex-ante or ex-post? What risk-free rate? What time period? |
| "beta" | Against which benchmark? ^GSPC (S&P 500) or another index? What estimation window? |
| "performance" | Absolute return, risk-adjusted return, or relative to a benchmark? |
| "stocks" | All equities? US only? Include Oslo Bors? Exclude indices? |
| "last quarter" | Most recent completed quarter, or trailing 3 months from today? |
| "YTD" | Year-to-date from January 1 of the current year |

If the user's intent is clear from context, proceed without asking. If genuinely ambiguous, ask ONE focused clarifying question — do not ask multiple.

### Step 2: RESOLVE — Check the semantic layer

Query the semantic layer (`semantic_layer/metrics.yaml`) for a governed metric definition:

1. Look up the metric by name or keyword match
2. If found: use the semantic compiler to generate SQL — this is the authoritative implementation
3. Note the metric's `notes` field for important caveats (e.g., currency warnings, NULL edge cases)
4. If not found: proceed to Step 3

### Step 3: FIND — Route to reference documentation

If the semantic layer doesn't cover the requested analysis:

1. Load the knowledge skill (`skills/knowledge.md`) routing table
2. Identify the primary reference doc for the question's domain
3. Load that reference doc and follow its guidance for table selection, joins, and gotchas

### Step 4: QUERY — Generate SQL

Write DuckDB SQL following these rules:

**Table aliases:**
- `fp` for `fact_daily_prices`
- `s` for `dim_securities`
- `dc` for `dim_calendar`

**Mandatory filters:**
- Always filter by `date` range — never query all-time unless explicitly asked
- When the user says "stocks," add `s.security_type = 'equity'` to exclude indices
- When comparing across tickers, check that currencies match (join `dim_securities` and verify `currency`)

**Join patterns:**
- Price data + security metadata: `fact_daily_prices fp JOIN dim_securities s ON fp.ticker = s.ticker`
- Price data + calendar: `fact_daily_prices fp JOIN dim_calendar dc ON fp.date = dc.date AND s.exchange = dc.exchange`
- Two-ticker comparison (beta, correlation): self-join `fact_daily_prices` on matching dates

**NULL awareness:**
- `daily_simple_return` and `daily_log_return` are NULL for the first trading day per ticker
- Indices have NULL `sector`, `industry`, `market_cap` in `dim_securities`
- Volume is not meaningful for indices

**DuckDB-specific syntax:**
- Window functions: `OVER (PARTITION BY ... ORDER BY ...)`
- Aggregate + window mix: use CTEs — DuckDB does not allow window functions inside aggregate calls
- Date functions: `DATE_TRUNC('month', date)`, `EXTRACT(YEAR FROM date)`, `LAST_DAY(date)`
- Regression: `REGR_SLOPE()`, `REGR_R2()`, `CORR()`

### Step 5: REVIEW — Adversarial self-check

Before executing, review the SQL for these common errors:

| Error Type | Check |
|---|---|
| Wrong grain | Does GROUP BY match the expected output grain? One row per ticker? Per ticker-date? |
| Missing filter | Did you filter for `security_type = 'equity'` when the user asked about "stocks"? |
| Date range | Is the date filter correct? "Q1 2024" = 2024-01-01 to 2024-03-31, not 2024-03-28 |
| Currency mixing | Are you comparing price-denominated values across USD and NOK tickers? |
| Calendar mismatch | If using `dim_calendar`, did you join on `exchange` too? |
| NULL handling | Are you using NULLIF to avoid division by zero? Filtering out NULL returns for first-day rows? |
| Index confusion | Are index tickers (^GSPC, etc.) included when they shouldn't be? |
| Window vs aggregate | Did you put a window function inside MIN/MAX/AVG? Use a CTE instead. |
| NULL prices in LAST/FIRST | When using `LAST(adjusted_close ORDER BY date)`, add `AND adjusted_close IS NOT NULL` — Oslo Bors tickers often have NULL prices on partial trading days. |

### Step 6: DELIVER — Format the response

Structure every response with:

1. **Answer** — Direct answer to the question in plain language
2. **Methodology** — Brief explanation of how the metric was computed (which formula, which table, what filters)
3. **SQL** — The executed query (for transparency)
4. **Data table** — Query results formatted as a table
5. **Provenance footer** — Include:
   - **Source:** fact_daily_prices / dim_securities (which tables used)
   - **Data freshness:** most recent date in the query result
   - **Currency:** USD / NOK / mixed
   - **Coverage:** which tickers were included/excluded and why

## Reusable Analysis Patterns

### Return Decomposition
Break down portfolio return by contribution of each holding:
```sql
-- Weight × return per ticker, summed = portfolio return
SELECT ticker, weight * cumulative_return AS contribution
```

### Rolling Metric (e.g., 60-day rolling volatility)
```sql
SELECT ticker, date,
    STDDEV(daily_log_return) OVER (
        PARTITION BY ticker ORDER BY date
        ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
    ) * SQRT(252) AS rolling_vol_60d
FROM fact_daily_prices
```

### Period Comparison (Q1 vs Q2)
```sql
WITH q1 AS (...WHERE date BETWEEN '2024-01-01' AND '2024-03-31'),
     q2 AS (...WHERE date BETWEEN '2024-04-01' AND '2024-06-30')
SELECT q1.ticker, q1.value AS q1_value, q2.value AS q2_value,
       q2.value - q1.value AS change
FROM q1 JOIN q2 ON q1.ticker = q2.ticker
```

### Top-N Ranking
```sql
SELECT ticker, metric_value,
    RANK() OVER (ORDER BY metric_value DESC) AS rank
FROM (... aggregation subquery ...)
QUALIFY rank <= 10
```

### Cross-Exchange Comparison
```sql
-- Always note the currency difference in the response
SELECT s.exchange, s.currency, AVG(...) AS avg_metric
FROM fact_daily_prices fp
JOIN dim_securities s ON fp.ticker = s.ticker
WHERE s.security_type = 'equity'
GROUP BY s.exchange, s.currency
```
