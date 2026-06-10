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
│  • Adversarial review sub-agent (9 error checks)             │
│  • Provenance footer (source tier, freshness, confidence)    │
│  • Offline eval suite (52 Q&A pairs, CI-gated)               │
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
│  • Semantic layer (13 governed metric definitions in YAML)   │
│  • Dimension + segment definitions                           │
│  • YAML-to-SQL compiler                                      │
└──────────────────────┬───────────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────────┐
│  LAYER 1: DATA FOUNDATIONS                                   │
│  • DuckDB warehouse with canonical tables                    │
│  • Dimensional model (fact_daily_prices, dim_securities...)  │
│  • Data quality checks (freshness, completeness, anomalies)  │
└──────────────────────────────────────────────────────────────┘
```

## How It Works

Raw LLMs writing SQL get the answer wrong ~79% of the time. Anthropic identified three failure modes responsible for almost all errors, and this project solves each with a dedicated layer:

| Failure Mode | What Goes Wrong | Solution |
|---|---|---|
| **Concept ambiguity** | "Returns" could mean simple, log, cumulative, or annualized — the LLM picks the wrong one | **Semantic layer** with 13 governed metric definitions, each with exactly one canonical SQL expression |
| **Retrieval failure** | The right table exists but the LLM doesn't find it among dozens of options | **Skills** (markdown playbooks) that narrow the search to a handful of curated reference docs per domain |
| **Unchecked errors** | Wrong joins, missing filters, currency mixing, NULL edge cases | **Adversarial reviewer** that checks every query against 9 common error types before execution |

### Walkthrough: What Happens When You Ask a Question

```
You: "What was EQNR.OL's cumulative return in Q3 2024?"
```

**Step 1 — CLARIFY:** Claude interprets the question — ticker: EQNR.OL, metric: cumulative return, period: Q3 2024 (Jul 1 - Sep 30).

**Step 2 — RESOLVE:** The semantic resolver matches "cumulative return" to the governed `cumulative_return` metric in `metrics.yaml`. The YAML-to-SQL compiler produces:
```sql
SELECT fp.ticker,
    (LAST(adjusted_close ORDER BY date) / FIRST(adjusted_close ORDER BY date)) - 1 AS value
FROM fact_daily_prices fp
WHERE fp.ticker IN ('EQNR.OL')
  AND fp.date >= '2024-07-01' AND fp.date <= '2024-09-30'
GROUP BY fp.ticker
```
The LLM never invents the formula — it comes from the governed definition.

**Step 3 — REVIEW:** The adversarial reviewer catches that `LAST()` is used without an `IS NOT NULL` filter. This matters because Oslo Bors tickers can have NULL adjusted_close on partial trading days. It adds `AND fp.adjusted_close IS NOT NULL`.

**Step 4 — EXECUTE & DELIVER:** The query runs against DuckDB and the result is formatted with a provenance footer:

```
Answer:  EQNR.OL cumulative return: -8.23%
Source:  fact_daily_prices
Currency: NOK
Review:  Revised — added NULL price filter for Oslo Bors ticker
```

For questions the semantic layer *doesn't* cover (e.g., "build a correlation matrix for 5 tech stocks"), the agent falls through to Claude-powered SQL generation, guided by the domain-specific reference docs from the skills layer.

### Project Structure

```
├── data/                          # Layer 1: Data Foundations
│   ├── ingestion/
│   │   ├── ingest_prices.py       #   yfinance → DuckDB (24 tickers, 5 years)
│   │   └── quality_checks.py      #   Freshness, completeness, anomaly checks
│   └── warehouse.duckdb           #   DuckDB warehouse (gitignored, built by ingestion)
│
├── semantic_layer/                # Layer 2: Sources of Truth
│   ├── metrics.yaml               #   13 governed metric definitions
│   ├── dimensions.yaml            #   Security + time dimensions
│   ├── segments.yaml              #   Named filters (us_equities, tech_stocks, ...)
│   └── compiler.py                #   YAML → executable DuckDB SQL
│
├── skills/                        # Layer 3: Skills
│   ├── knowledge.md               #   Router: question domain → reference doc
│   ├── analyst.md                 #   6-step procedural playbook
│   └── references/                #   Domain-specific guides with gotchas + SQL patterns
│       ├── returns_and_risk.md
│       ├── portfolio_analytics.md
│       ├── market_overview.md
│       └── fundamentals.md
│
├── agent/                         # Layer 4: Agent + Validation
│   ├── orchestrator.py            #   Main pipeline: clarify → resolve → query → review → deliver
│   ├── semantic_resolver.py       #   NL question → governed metric + compiled SQL
│   ├── sql_generator.py           #   Claude-powered SQL for complex questions
│   ├── reviewer.py                #   Adversarial review (5 static + 9 LLM checks)
│   └── response_formatter.py      #   Provenance footer + structured output
│
├── evals/                         # Offline Eval Suite
│   ├── fixtures/                  #   52 Q&A test cases across 5 domains
│   ├── run_evals.py               #   Eval runner (metric resolution + SQL correctness)
│   └── ablation.py                #   A/B testing for skill changes
│
└── .github/workflows/eval_ci.yml  # CI: runs evals on every PR, blocks on regressions
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

# Set your API key for the agent
export ANTHROPIC_API_KEY=your-key-here
```

## Usage

```bash
# Web UI (recommended)
streamlit run app/streamlit_app.py

# Interactive CLI
python -m agent.orchestrator

# Ask a question programmatically
python -c "
from agent import AnalyticsAgent
agent = AnalyticsAgent()
response = agent.answer('What was AAPL Sharpe ratio in 2024?')
print(response.to_markdown())
"

# Run offline evals (resolution-only, no API key needed)
python -m evals.run_evals --no-llm --verbose

# Run full evals (requires ANTHROPIC_API_KEY and warehouse.duckdb)
python -m evals.run_evals --verbose --save

# Compare eval results (ablation testing)
python -m evals.ablation baseline.json candidate.json
```

## Agent Pipeline

The orchestrator follows the analyst skill's 6-step workflow for every question:

1. **CLARIFY** — Disambiguate the question (returns type, time period, benchmark)
2. **RESOLVE** — Check the semantic layer for a governed metric definition
3. **FIND** — Route to the correct reference doc via the knowledge skill
4. **QUERY** — Generate SQL (from semantic compiler or LLM with skill guidance)
5. **REVIEW** — Adversarial check for 9 common error types (wrong grain, missing filters, currency mixing, NULL handling, etc.)
6. **DELIVER** — Execute, format results, append provenance footer

## Data Model

| Table | Grain | Description |
|-------|-------|-------------|
| `fact_daily_prices` | ticker x date | OHLCV + adjusted close + computed daily returns |
| `dim_securities` | ticker | Name, sector, industry, exchange, currency, security_type |
| `dim_calendar` | date x exchange | Trading days with year, quarter, month, day attributes |
| `raw_daily_prices` | ticker x date | Staging table with raw yfinance data |

**Ticker universe:** US large-caps (AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA, JPM, V, JNJ, PG, UNH, HD, MA, PFE), Oslo Bors (EQNR.OL, DNB.OL, MOWI.OL, ORK.OL, TEL.OL, AKRBP.OL), indices (^GSPC, ^OEX, ^IXIC).

## Semantic Layer

13 governed metrics across returns, volatility, risk-adjusted, drawdown, correlation, and price/volume categories. Each metric has a canonical SQL expression, parameter defaults, and documented gotchas.

| Category | Metrics |
|----------|---------|
| Returns | daily_simple_return, daily_log_return, cumulative_return, total_return_index |
| Volatility | annualized_volatility |
| Risk-adjusted | sharpe_ratio, sortino_ratio |
| Drawdown | max_drawdown |
| Correlation | beta, correlation |
| Price/Volume | average_daily_volume, price_range, vwap |

## Eval Suite

52 eval cases across 4 domains, testing metric resolution, SQL correctness, and answer accuracy:

| Domain | Cases | Tests |
|--------|-------|-------|
| Returns & Risk | 15 | Cumulative return, volatility, Sharpe, Sortino, drawdown |
| Risk / Beta | 12 | Beta, correlation, R-squared, rolling windows |
| Portfolio | 8 | Relative performance, cross-exchange, sector comparison |
| Market Overview | 12 | Index performance, sector averages, market breadth |
| Fundamentals | 5 | Correctly handles "data not available" |

## Project Status

- [x] Phase 1: Data Foundations — DuckDB warehouse, ingestion pipeline, quality checks
- [x] Phase 2: Semantic Layer — 13 YAML metric definitions, dimensions, segments, SQL compiler
- [x] Phase 3: Skills — Analyst playbook, knowledge routing, 4 domain reference docs
- [x] Phase 4: Agent + Validation — Orchestrator, adversarial review, 52-case eval suite, CI
- [x] Phase 5: UI + Polish — Streamlit app with reasoning trace, SQL display, and provenance

## Tech Stack

- **Python 3.11+** with pip/uv
- **DuckDB** — embedded analytical warehouse
- **yfinance** — free financial market data
- **Claude API** (Anthropic SDK) — LLM agent for text-to-SQL
- **Streamlit** — web UI (Phase 5)
- **pytest** — offline eval suite
