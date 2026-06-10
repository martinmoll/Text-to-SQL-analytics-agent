# Self-Service Financial Analytics Agent

## Project Overview

Build an **agentic text-to-SQL analytics system** for financial market data that implements the architecture Anthropic uses internally to automate 95% of their business analytics queries. Instead of corporate data, you use publicly available financial data (via `yfinance`) — making it fully open-sourceable.

**Why this project is CV gold:** it sits at the intersection of data engineering, LLM agent design, and quantitative finance. It demonstrates you understand modern analytics engineering patterns, can build production-grade AI systems, and can evaluate them rigorously — all directly relevant to a Business Analytics master's.

---

## Architecture (mirrors Anthropic's 4-layer stack)

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

---

## The Three Failure Modes You're Solving

Anthropic identified these as responsible for the overwhelming majority of wrong answers. Your project should explicitly demonstrate solutions to each:

| Failure Mode | What Goes Wrong | Your Solution |
|---|---|---|
| **Concept ↔ Entity Ambiguity** | "Returns" could mean daily log returns, simple returns, total returns, excess returns over risk-free... the LLM picks the wrong one | Semantic layer with governed metric definitions; canonical tables with single-source-of-truth per concept |
| **Data Staleness** | Yesterday's prices aren't loaded yet; a ticker got delisted; a column name changed | Freshness checks before query; scheduled ingestion with recency assertions; colocated docs that update with schema changes |
| **Retrieval Failure** | The right table exists and is documented, but the agent doesn't find it among dozens of options | Skills that narrow the search space to a handful of curated reference docs per domain |

---

## Tech Stack

| Component | Tool | Why |
|---|---|---|
| Warehouse | **DuckDB** | Embedded, zero-config, fast analytical queries, perfect for a portfolio project |
| Ingestion | **yfinance** + Python scripts | Free financial data; you already know the `.OL` suffix convention for Oslo Børs |
| Semantic Layer | **Custom YAML definitions** | Lightweight; demonstrates the concept without enterprise tooling overhead |
| LLM Agent | **Claude API** (or OpenAI) | Text-to-SQL generation with skill-guided reasoning |
| Skills | **Markdown files** in repo | Exactly what Anthropic uses |
| Evals | **pytest** + JSON fixtures | Offline eval suite with CI integration |
| UI | **Streamlit** or **FastAPI + HTMX** | Quick to build; shows the end-user experience |
| Orchestration | **Python** | Glue code, agent loop, validation pipeline |

---

## Repo Structure

```
finance-analytics-agent/
├── README.md                          # Project overview + architecture diagram
├── pyproject.toml                     # Dependencies
│
├── data/
│   ├── ingestion/
│   │   ├── ingest_prices.py           # yfinance → DuckDB loader
│   │   ├── ingest_fundamentals.py     # Balance sheet, income statement
│   │   └── quality_checks.py          # Freshness, completeness, anomaly detection
│   ├── models/
│   │   ├── dim_securities.sql         # Ticker → name, sector, exchange, currency
│   │   ├── dim_calendar.sql           # Trading days, quarters, fiscal periods
│   │   ├── fact_daily_prices.sql      # OHLCV, adjusted close, daily returns
│   │   ├── fact_fundamentals.sql      # Quarterly financials
│   │   └── agg_monthly_returns.sql    # Pre-aggregated monthly return series
│   └── warehouse.duckdb              # (gitignored, built by ingestion)
│
├── semantic_layer/
│   ├── metrics.yaml                   # Governed metric definitions
│   ├── dimensions.yaml                # Governed dimension definitions
│   ├── segments.yaml                  # Named filters (e.g., "tech_large_cap")
│   └── compiler.py                    # YAML → SQL compiler
│
├── skills/
│   ├── knowledge.md                   # Top-level router: domain → reference doc
│   ├── analyst.md                     # Procedural playbook (clarify → find → query → review)
│   └── references/
│       ├── returns_and_risk.md        # Tables, joins, gotchas for return metrics
│       ├── fundamentals.md            # P/E, EV/EBITDA, how to handle fiscal periods
│       ├── portfolio_analytics.md     # Correlation, beta, portfolio construction
│       └── market_overview.md         # Indices, sector aggregations
│
├── agent/
│   ├── orchestrator.py                # Main agent loop
│   ├── semantic_resolver.py           # Check semantic layer first
│   ├── sql_generator.py              # Skill-guided text-to-SQL
│   ├── reviewer.py                    # Adversarial review sub-agent
│   └── response_formatter.py         # Provenance footer, confidence tier
│
├── evals/
│   ├── fixtures/
│   │   ├── returns_evals.json         # Q&A pairs for return metrics
│   │   ├── risk_evals.json            # Q&A pairs for risk metrics
│   │   └── fundamentals_evals.json    # Q&A pairs for valuation metrics
│   ├── run_evals.py                   # Eval runner (scores query correctness)
│   ├── results/                       # Stored eval results (for time-series tracking)
│   └── ablation.py                    # A/B testing skill changes against eval set
│
├── app/
│   └── streamlit_app.py               # Web UI: ask a question, see SQL + result + provenance
│
└── .github/
    └── workflows/
        └── eval_ci.yml                # Run evals on every PR that touches skills/ or models/
```

---

## Implementation Phases

### Phase 1: Data Foundations (Week 1)

Build the warehouse. This is deliberately simple — the point is *governance*, not scale.

**What to build:**
- `ingest_prices.py` — pull daily OHLCV for ~50 tickers (mix of US large-caps, some Oslo Børs `.OL` stocks, a few indices) via yfinance into DuckDB
- `ingest_fundamentals.py` — quarterly income statement + balance sheet for the same tickers
- Dimensional model: `dim_securities` (ticker, name, sector, market_cap_tier, exchange), `dim_calendar` (date, is_trading_day, quarter, fiscal_year), `fact_daily_prices` (grain: ticker × date), `fact_fundamentals` (grain: ticker × fiscal_quarter)
- `quality_checks.py` — assert freshness (max date within 3 days of today), completeness (no ticker with >5% missing days), basic anomaly flags (daily return > 20% gets flagged)

**Key Anthropic principle applied:** *"Create canonical datasets... curate a small set of canonical, single source-of-truth datasets... then aggressively deprecate the near-duplicates."* You have ONE `fact_daily_prices` table, not three overlapping views.

### Phase 2: Semantic Layer (Week 1-2)

This is where you collapse ambiguity. Define every metric once, with one governed SQL implementation.

**Example `metrics.yaml`:**
```yaml
metrics:
  daily_simple_return:
    description: "Simple daily return: (close - prev_close) / prev_close"
    table: fact_daily_prices
    expression: "(adjusted_close - LAG(adjusted_close) OVER (PARTITION BY ticker ORDER BY date)) / LAG(adjusted_close) OVER (PARTITION BY ticker ORDER BY date)"
    unit: ratio
    notes: "Uses adjusted close to account for splits/dividends"

  daily_log_return:
    description: "Log daily return: ln(close / prev_close)"
    table: fact_daily_prices
    expression: "LN(adjusted_close / LAG(adjusted_close) OVER (PARTITION BY ticker ORDER BY date))"
    unit: ratio
    notes: "Preferred for multi-period aggregation due to additivity"

  annualized_volatility:
    description: "Annualized volatility from daily log returns over a trailing window"
    table: fact_daily_prices
    expression: "STDDEV(daily_log_return) * SQRT(252)"
    default_window: "252 trading days"
    unit: ratio

  sharpe_ratio:
    description: "Annualized excess return over risk-free rate, divided by annualized volatility"
    depends_on: [daily_log_return, annualized_volatility]
    parameters:
      risk_free_rate: "Uses 3-month US Treasury yield from dim_risk_free_rates"
    notes: "ALWAYS clarify: is the user asking for ex-ante or ex-post Sharpe?"

  beta:
    description: "CAPM beta: covariance of asset returns with market returns / variance of market returns"
    depends_on: [daily_log_return]
    parameters:
      market_benchmark: "Default: SPY. Always confirm with user."
      window: "Default: 252 trading days rolling"
    notes: "Multiple estimation methods exist (OLS, EWMA, Kalman). Default to OLS rolling regression unless user specifies."
```

**`compiler.py`** takes a metric name + parameters (ticker, date range, etc.) and compiles it into executable DuckDB SQL. The agent calls this first before falling back to raw SQL.

### Phase 3: Skills (Week 2-3)

This is the highest-leverage component. Anthropic's data went from 21% → 95%+ accuracy by adding skills.

**`knowledge.md`** (the router):
```markdown
# Financial Analytics Knowledge Skill

## Routing
- IF question involves returns, volatility, Sharpe, Sortino, drawdown
  → load `references/returns_and_risk.md`
- IF question involves P/E, EV/EBITDA, revenue, margins, book value
  → load `references/fundamentals.md`
- IF question involves correlation, beta, portfolio weights, diversification
  → load `references/portfolio_analytics.md`
- IF question involves index performance, sector rotation, market breadth
  → load `references/market_overview.md`

## Mandatory First Step
ALWAYS check the semantic layer (semantic_layer/metrics.yaml) FIRST.
Only fall back to raw SQL via reference docs if the semantic layer
has no coverage for the requested metric.
```

**`analyst.md`** (the procedure):
```markdown
# Analyst Skill — Procedural Playbook

## Workflow
1. CLARIFY: Disambiguate the question
   - "Returns" → simple or log? Total or excess?
   - Time period → exact dates or trailing window?
   - Benchmark → which index? SPY? OSEBX?
2. RESOLVE: Check semantic layer for governed metric
3. FIND: If no semantic coverage, load knowledge skill → reference doc
4. QUERY: Generate SQL using reference doc guidance
5. REVIEW: Pass SQL to adversarial reviewer before executing
6. DELIVER: Show result + methodology + provenance footer

## Reusable Analysis Patterns
- Return decomposition (contribution by holding)
- Rolling beta estimation (OLS with configurable window)
- Drawdown analysis (peak-to-trough, recovery period)
- Correlation heatmap (pairwise, with significance flags)
- Sector-relative performance (vs. equal-weight sector basket)
```

**Example reference doc (`references/returns_and_risk.md`):**
Follow the skeleton from the article — business context, entity grain, standard filters, key tables with grain/scope/usage, gotchas, common query patterns.

### Phase 4: Agent + Validation (Week 3-4)

**The agent loop (`orchestrator.py`):**
1. Receive natural language question
2. Load analyst skill → follow the workflow
3. Check semantic layer via `semantic_resolver.py`
4. If no coverage → load knowledge skill → route to reference doc → generate SQL
5. Pass generated SQL to `reviewer.py` (adversarial sub-agent that checks for common errors: wrong join, missing filters, grain mismatch)
6. Execute query against DuckDB
7. Format response with provenance footer

**Offline evals (`evals/`):**
Create ~30-50 question/answer pairs per domain. Pin to snapshot dates.
```json
{
  "question": "What was AAPL's annualized volatility in Q1 2024?",
  "expected_metric": "annualized_volatility",
  "expected_table": "fact_daily_prices",
  "expected_filters": {"ticker": "AAPL", "date_range": ["2024-01-02", "2024-03-28"]},
  "ground_truth": 0.187,
  "tolerance": 0.005,
  "snapshot_date": "2024-04-01"
}
```

**CI integration:** GitHub Actions runs evals on every PR that touches `skills/`, `semantic_layer/`, or `data/models/`. PR can't merge if accuracy drops.

### Phase 5: UI + Polish (Week 4)

Streamlit app that:
- Takes a natural language question
- Shows the agent's reasoning trace (which skill loaded, semantic layer hit/miss)
- Displays the generated SQL
- Shows the query result as a table or chart
- Appends a provenance footer (source tier, data freshness, confidence)

---

## What Makes This Stand Out on a CV

1. **Architecture over hacking** — you're not just "using ChatGPT to write SQL." You've built a governed system with explicit failure mode mitigation.
2. **Eval-driven development** — you can quantify your system's accuracy and show how specific changes (skill edits, semantic layer additions) moved the needle via ablation.
3. **Domain depth** — the financial metrics demonstrate genuine quant knowledge (CAPM beta, Sharpe ratio, rolling estimation), not toy examples.
4. **Production patterns** — semantic layer, adversarial review, provenance tracking, CI-gated evals. These are patterns used by Anthropic, dbt Labs, and top data teams.
5. **Open source friendly** — uses free data, free tooling (DuckDB), and can be fully reproduced by anyone who clones the repo.

---

## How to Frame It

**GitHub repo name:** `finance-analytics-agent` or `text-to-sql-analytics-agent`

**One-liner for CV:**
> Built an agentic text-to-SQL system for financial analytics with a governed semantic layer, LLM skill routing, adversarial validation, and offline eval suite — achieving 95%+ query accuracy across 150+ eval cases.

**README should include:** architecture diagram, a GIF of the Streamlit UI in action, accuracy metrics from your eval suite, and a section on "Lessons Learned" discussing which of the three failure modes was hardest to solve in practice.

---

## Stretch Goals (if you want to go further)

- **MCP server**: expose your analytics agent as an MCP server so it can be called from Claude Code, Slack, or other surfaces — directly mirroring Anthropic's "consistent experience across all surfaces" principle
- **Ablation dashboard**: a page in the Streamlit app showing eval accuracy over time, per-domain breakdowns, and before/after comparisons for skill changes
- **Oslo Børs coverage**: add `.OL` tickers and Norwegian business context to skills, making it bilingual (English/Norwegian) — unique differentiator
- **Kalman filter beta estimation**: add this as an advanced metric in the semantic layer, with a reference doc explaining when to use it vs. rolling OLS — ties directly to your quant finance exploration
