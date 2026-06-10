# Claude Code Opening Prompt

Paste this into Claude Code as your first message:

---

Read the project plan in `self-service-analytics-agent-project-plan.md` before doing anything. That document describes the full architecture for a self-service financial analytics agent inspired by Anthropic's internal analytics stack.

## What we're building

A text-to-SQL analytics agent for financial market data that implements the 4-layer agentic analytics stack from Anthropic's blog post. The system takes natural language questions about stocks and markets, resolves them through a governed semantic layer and markdown skill files, generates SQL against a DuckDB warehouse, runs adversarial review, and returns answers with provenance footers.

## Tech decisions (locked in)

- **Python 3.11+**, use `uv` for dependency management if available, otherwise `pip`
- **DuckDB** as the analytical warehouse (embedded, no server)
- **yfinance** for market data ingestion
- **Claude API** (Anthropic SDK) for the LLM agent — use `claude-sonnet-4-20250514`
- **Streamlit** for the web UI (Phase 5, not now)
- **pytest** for evals
- Keep everything in a single repo, no microservices

## Start with Phase 1: Project scaffolding + Data foundations

Do this step by step, confirming with me after each sub-step:

1. **Scaffold the repo structure** as described in the project plan. Create the directory tree with placeholder README files in each folder explaining that folder's purpose. Initialize `pyproject.toml` with core dependencies (duckdb, yfinance, anthropic, pytest, streamlit, pyyaml, pandas).

2. **Build `data/ingestion/ingest_prices.py`**:
   - Pull daily OHLCV + adjusted close for a starter universe of ~30 tickers: a mix of US large-caps (AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA, JPM, V, JNJ, PG, UNH, HD, MA, PFE), some Oslo Børs stocks (EQNR.OL, DNB.OL, MOWI.OL, ORK.OL, TEL.OL, AKRBP.OL), and indices (^GSPC, ^OEX, ^IXIC).
   - Load 5 years of history into DuckDB.
   - Store in a `raw_daily_prices` staging table first, then transform into the canonical `fact_daily_prices` table with computed columns (daily_simple_return, daily_log_return).
   - Add `dim_securities` with ticker, name, sector, exchange, currency — populated from yfinance's `.info` metadata.
   - Add `dim_calendar` as a trading-day calendar derived from the dates actually present in the price data.

3. **Build `data/ingestion/quality_checks.py`**:
   - Freshness check: assert max date in fact_daily_prices is within 5 trading days of today
   - Completeness check: flag any ticker with >5% missing trading days
   - Anomaly flag: flag any row where abs(daily_simple_return) > 0.20
   - Print a clean summary report when run

4. **Write the main README.md** with:
   - Project title and one-liner description
   - Architecture diagram (use the ASCII one from the plan for now)
   - Setup instructions (clone, install deps, run ingestion)
   - Status section with checkboxes for each phase

After scaffolding and Phase 1, pause. Don't move to the semantic layer yet — I want to review the data model first.
