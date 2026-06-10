# Fundamentals — Reference Document

## Business Context

Fundamental analysis uses financial statement data (income statement, balance sheet, cash flow) to compute valuation and profitability metrics. This enables questions like "What is AAPL's P/E ratio?" or "Which stocks have the highest revenue growth?"

## Current Data Status

> **IMPORTANT: Fundamentals data is NOT YET INGESTED.**
>
> The `fact_fundamentals` table does not exist in the warehouse yet. This is planned for a future phase (`data/ingestion/ingest_fundamentals.py`).
>
> If a user asks a fundamentals question, respond honestly:
> 1. Explain that fundamental data (income statement, balance sheet) is not yet available in the warehouse
> 2. Note which metrics you *can* compute from price data alone (e.g., price change, volatility, Sharpe — but NOT P/E, revenue, margins)
> 3. Suggest the user check back after the fundamentals ingestion is built

## What CAN Be Answered From Price Data

Some metrics that sound like "fundamentals" can actually be approximated or partially answered using only price + security metadata:

| Question | Can Answer? | How |
|---|---|---|
| "What's the market cap of AAPL?" | Partial | `dim_securities.market_cap` has a snapshot from ingestion time — but it's static, not current |
| "Which sector performed best?" | Yes | Use `dim_securities.sector` joined with `fact_daily_prices` returns |
| "What's the dividend yield?" | No | Requires dividend data not in the warehouse |
| "What's the P/E ratio?" | No | Requires earnings data |
| "Revenue growth?" | No | Requires income statement data |

## Planned Schema (for future reference)

When `fact_fundamentals` is built, it will have this structure:

| Column | Type | Description |
|---|---|---|
| ticker | VARCHAR | Security identifier |
| fiscal_period | VARCHAR | e.g., "2024-Q1", "2024-FY" |
| period_end_date | DATE | Last day of the fiscal period |
| revenue | DOUBLE | Total revenue |
| net_income | DOUBLE | Net income |
| ebitda | DOUBLE | Earnings before interest, taxes, depreciation, amortization |
| total_assets | DOUBLE | Balance sheet total assets |
| total_debt | DOUBLE | Total debt |
| total_equity | DOUBLE | Shareholders' equity |
| eps | DOUBLE | Earnings per share |
| dividends_per_share | DOUBLE | Dividends declared per share |

**Grain:** One row per ticker per fiscal period.

## Gotchas (for when fundamentals are available)

### 1. Fiscal year ≠ calendar year
Some companies have fiscal years ending in non-December months (e.g., Apple's FY ends in September). Always use `period_end_date` for time-based comparisons, not the fiscal period label.

### 2. Trailing twelve months (TTM)
Many valuation ratios (P/E, EV/EBITDA) use TTM figures — sum of the last 4 quarters. Do not use a single quarter's earnings to compute P/E.

### 3. Currency in fundamentals
Revenue and earnings will be in the company's reporting currency, which may differ from the trading currency. Oslo Bors companies typically report in NOK.

### 4. Negative earnings
P/E ratio is undefined when earnings are negative. Filter these out or flag them rather than showing a negative P/E, which is misleading.
