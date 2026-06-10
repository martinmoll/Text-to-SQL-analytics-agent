# Semantic Layer

Governed metric, dimension, and segment definitions. The compiler translates these into executable DuckDB SQL. The agent checks this layer first before falling back to raw SQL generation.

- `metrics.yaml` — 13 canonical metrics (returns, volatility, Sharpe, Sortino, beta, correlation, drawdown, etc.)
- `dimensions.yaml` — Security and time dimensions with notes on currency mixing and calendar gotchas
- `segments.yaml` — Named filters for slicing by exchange, sector, currency, and security type
- `compiler.py` — YAML → SQL compiler with support for aggregates, window functions, self-joins, and CTEs
