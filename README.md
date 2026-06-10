# Text-to-SQL Analytics Agent

An agentic text-to-SQL system for financial market data that implements the 4-layer analytics stack from [Anthropic's approach to agentic analytics](https://www.anthropic.com/engineering/analytics-with-claude). Takes natural language questions about stocks and markets, resolves them through a governed semantic layer and markdown skill files, generates SQL against a DuckDB warehouse, runs adversarial review, and returns answers with provenance footers.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  USER QUESTION                                               │
│  "What was the Sharpe ratio of AAPL vs MSFT last quarter?"   │
└──────────────────────┬───────────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────────┐
│  LAYER 4: VALIDATION                                         │
│  • Adversarial review sub-agent                              │
│  • Provenance footer (source tier, freshness, confidence)    │
│  • Offline eval suite (question/answer pairs, CI-gated)      │
└──────────────────────┬───────────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────────┐
│  LAYER 3: SKILLS (markdown playbooks)                        │
│  • Knowledge skill → routes to correct domain reference doc  │
│  • Analyst skill → step-by-step procedure + reusable         │
│    analysis patterns (return decomposition, risk metrics)     │
└──────────────────────┬───────────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────────┐
│  LAYER 2: SOURCES OF TRUTH                                   │
│  • Semantic layer (YAML metric definitions)                  │
│  • Table lineage graph                                       │
│  • Business context glossary                                 │
└──────────────────────┬───────────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────────┐
│  LAYER 1: DATA FOUNDATIONS                                   │
│  • DuckDB warehouse with canonical tables                    │
│  • Dimensional model (fact_daily_prices, dim_securities...)  │
│  • Data quality checks (freshness, completeness, anomalies)  │
└──────────────────────────────────────────────────────────────┘
```

## Setup

```bash
# Clone the repo
git clone https://github.com/martinmoll/Text-to-SQL-analytics-agent.git
cd Text-to-SQL-analytics-agent

# Install dependencies
pip install -e .

# Run data ingestion (pulls 5 years of daily prices for ~24 tickers)
python data/ingestion/ingest_prices.py

# Run quality checks
python data/ingestion/quality_checks.py
```

## Data Model

| Table | Grain | Description |
|-------|-------|-------------|
| `fact_daily_prices` | ticker x date | OHLCV + adjusted close + computed daily returns |
| `dim_securities` | ticker | Name, sector, industry, exchange, currency |
| `dim_calendar` | date | Trading days with year, quarter, month, day attributes |
| `raw_daily_prices` | ticker x date | Staging table with raw yfinance data |

**Ticker universe:** US large-caps (AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA, JPM, V, JNJ, PG, UNH, HD, MA, PFE), Oslo Bors (EQNR.OL, DNB.OL, MOWI.OL, ORK.OL, TEL.OL, AKRBP.OL), indices (^GSPC, ^OEX, ^IXIC).

## Project Status

- [x] Phase 1: Data Foundations — DuckDB warehouse, ingestion pipeline, quality checks
- [ ] Phase 2: Semantic Layer — YAML metric definitions, SQL compiler
- [ ] Phase 3: Skills — Markdown playbooks, knowledge routing, reference docs
- [ ] Phase 4: Agent + Validation — Orchestrator, adversarial review, offline evals
- [ ] Phase 5: UI + Polish — Streamlit app with reasoning trace and provenance

## Tech Stack

- **Python 3.11+** with pip/uv
- **DuckDB** — embedded analytical warehouse
- **yfinance** — free financial market data
- **Claude API** (Anthropic SDK) — LLM agent for text-to-SQL
- **Streamlit** — web UI (Phase 5)
- **pytest** — offline eval suite
