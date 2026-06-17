"""
Semantic layer compiler: YAML metric definitions → executable DuckDB SQL.

The agent calls this before falling back to raw SQL generation. Given a metric
name and parameters (tickers, date range, etc.), it produces a ready-to-run
query against the warehouse.
"""

import yaml
from pathlib import Path
from dataclasses import dataclass
from datetime import date


SEMANTIC_DIR = Path(__file__).parent
METRICS_PATH = SEMANTIC_DIR / "metrics.yaml"
DIMENSIONS_PATH = SEMANTIC_DIR / "dimensions.yaml"
SEGMENTS_PATH = SEMANTIC_DIR / "segments.yaml"


@dataclass
class CompiledQuery:
    sql: str
    metric_name: str
    description: str
    notes: str | None
    parameters_used: dict


class SemanticCompiler:
    def __init__(self):
        self.metrics = self._load_yaml(METRICS_PATH).get("metrics", {})
        self.dimensions = self._load_yaml(DIMENSIONS_PATH).get("dimensions", {})
        self.segments = self._load_yaml(SEGMENTS_PATH).get("segments", {})

    def _load_yaml(self, path: Path) -> dict:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)

    def list_metrics(self) -> list[dict]:
        return [
            {
                "name": name,
                "description": defn.get("description", ""),
                "unit": defn.get("unit", ""),
                "table": defn.get("table", ""),
            }
            for name, defn in self.metrics.items()
        ]

    def list_segments(self) -> list[dict]:
        return [
            {
                "name": name,
                "description": defn.get("description", ""),
                "filter": defn.get("filter", ""),
            }
            for name, defn in self.segments.items()
        ]

    def get_metric(self, name: str) -> dict | None:
        return self.metrics.get(name)

    def get_segment_filter(self, name: str) -> str | None:
        seg = self.segments.get(name)
        return seg["filter"] if seg else None

    def compile(
        self,
        metric_name: str,
        tickers: list[str] | None = None,
        start_date: date | str | None = None,
        end_date: date | str | None = None,
        segment: str | None = None,
        parameters: dict | None = None,
    ) -> CompiledQuery:
        defn = self.metrics.get(metric_name)
        if not defn:
            available = ", ".join(self.metrics.keys())
            raise ValueError(f"Unknown metric '{metric_name}'. Available: {available}")

        table = defn["table"]
        params = parameters or {}

        # Resolve parameter defaults
        if "parameters" in defn:
            for pname, pdef in defn["parameters"].items():
                if pname not in params and "default" in pdef:
                    params[pname] = pdef["default"]

        # If it's a pre-computed column, just SELECT it
        select_expr = None
        if defn.get("column"):
            select_expr = defn["column"]
        elif defn.get("expression"):
            select_expr = defn["expression"].strip()
            for pname, pval in params.items():
                select_expr = select_expr.replace(f"{{{pname}}}", str(pval))

        # Build WHERE clauses
        where_parts = []
        if tickers:
            ticker_list = ", ".join(f"'{t}'" for t in tickers)
            where_parts.append(f"fp.ticker IN ({ticker_list})")
        if start_date:
            where_parts.append(f"fp.date >= '{start_date}'")
        if end_date:
            where_parts.append(f"fp.date <= '{end_date}'")
        if segment:
            seg_filter = self.get_segment_filter(segment)
            if seg_filter:
                where_parts.append(seg_filter)

        # Guard FIRST/LAST ordered aggregates against NULL adjusted_close
        # (Oslo Bors tickers can have NULLs on partial trading days)
        expr_upper = (select_expr or "").upper()
        if ("FIRST(" in expr_upper or "LAST(" in expr_upper) and "ADJUSTED_CLOSE" in expr_upper:
            where_parts.append("fp.adjusted_close IS NOT NULL")

        where_clause = ""
        if where_parts:
            where_clause = "WHERE " + " AND ".join(where_parts)

        # Decide if we need a JOIN to dim_securities
        needs_securities_join = segment is not None or self._filter_references_securities(where_parts)

        join_clause = ""
        if needs_securities_join:
            join_clause = "JOIN dim_securities ON fp.ticker = dim_securities.ticker"

        # Build the query based on metric type
        if defn.get("compile_mode") == "cte":
            sql = self._compile_cte_query(defn, join_clause, where_clause)
        elif defn.get("column") and select_expr:
            sql = self._compile_column_query(
                select_expr, table, join_clause, where_clause, tickers
            )
        elif defn.get("requires_join"):
            sql = self._compile_join_query(
                metric_name, defn, select_expr or "", params, where_parts, tickers, start_date, end_date
            )
        elif select_expr:
            sql = self._compile_aggregate_query(
                select_expr, table, join_clause, where_clause, tickers
            )
        else:
            raise ValueError(f"Metric '{metric_name}' has no expression, column, or CTE SQL defined")

        return CompiledQuery(
            sql=sql,
            metric_name=metric_name,
            description=defn.get("description", ""),
            notes=defn.get("notes"),
            parameters_used=params,
        )

    def _compile_cte_query(self, defn: dict, join_clause: str, where_clause: str) -> str:
        sql = defn["cte_sql"].strip()
        sql = sql.replace("{join_clause}", join_clause)
        sql = sql.replace("{where_clause}", where_clause)
        return sql

    def _filter_references_securities(self, where_parts: list[str]) -> bool:
        return any("dim_securities" in p for p in where_parts)

    def _compile_column_query(
        self, column: str, table: str, join_clause: str, where_clause: str,
        tickers: list[str] | None,
    ) -> str:
        return f"""SELECT
    fp.ticker,
    fp.date,
    fp.{column}
FROM {table} fp
{join_clause}
{where_clause}
ORDER BY fp.ticker, fp.date""".strip()

    def _compile_aggregate_query(
        self, expression: str, table: str, join_clause: str, where_clause: str,
        tickers: list[str] | None,
    ) -> str:
        return f"""SELECT
    fp.ticker,
    {expression} AS value
FROM {table} fp
{join_clause}
{where_clause}
GROUP BY fp.ticker
ORDER BY fp.ticker""".strip()

    def _compile_join_query(
        self, metric_name: str, defn: dict, expression: str, params: dict,
        where_parts: list[str], tickers: list[str] | None,
        start_date: date | str | None, end_date: date | str | None,
    ) -> str:
        if metric_name == "beta":
            benchmark = params.get("market_benchmark", "^GSPC")
            asset_wheres = list(where_parts)
            date_filters = []
            if start_date:
                date_filters.append(f"market.date >= '{start_date}'")
            if end_date:
                date_filters.append(f"market.date <= '{end_date}'")

            market_where = f"market.ticker = '{benchmark}'"
            if date_filters:
                market_where += " AND " + " AND ".join(date_filters)

            asset_where = ""
            if asset_wheres:
                asset_where = "WHERE " + " AND ".join(
                    w.replace("fp.", "asset.") for w in asset_wheres
                )

            return f"""SELECT
    asset.ticker,
    REGR_SLOPE(asset.daily_log_return, market.daily_log_return) AS beta,
    REGR_R2(asset.daily_log_return, market.daily_log_return) AS r_squared,
    COUNT(*) AS observation_count
FROM fact_daily_prices asset
JOIN fact_daily_prices market
    ON asset.date = market.date
    AND {market_where}
{asset_where}
GROUP BY asset.ticker
ORDER BY asset.ticker""".strip()

        elif metric_name == "correlation":
            if not tickers or len(tickers) < 2:
                raise ValueError("Correlation requires at least 2 tickers")
            t1, t2 = tickers[0], tickers[1]
            date_filters = []
            if start_date:
                date_filters.append(f"a.date >= '{start_date}'")
            if end_date:
                date_filters.append(f"a.date <= '{end_date}'")
            extra_where = ""
            if date_filters:
                extra_where = "AND " + " AND ".join(date_filters)

            return f"""SELECT
    a.ticker AS ticker_1,
    b.ticker AS ticker_2,
    CORR(a.daily_log_return, b.daily_log_return) AS correlation,
    COUNT(*) AS observation_count
FROM fact_daily_prices a
JOIN fact_daily_prices b
    ON a.date = b.date
WHERE a.ticker = '{t1}'
  AND b.ticker = '{t2}'
  {extra_where}
GROUP BY a.ticker, b.ticker""".strip()

        raise ValueError(f"No join query template for metric '{metric_name}'")

    def lookup(self, question: str) -> list[dict]:
        """Return metrics whose description or name matches keywords in the question."""
        words = question.lower().split()
        results = []
        for name, defn in self.metrics.items():
            desc = (defn.get("description", "") + " " + (defn.get("notes") or "")).lower()
            name_lower = name.lower().replace("_", " ")
            score = sum(1 for w in words if w in desc or w in name_lower)
            if score > 0:
                results.append({
                    "name": name,
                    "description": defn.get("description", ""),
                    "score": score,
                })
        results.sort(key=lambda x: x["score"], reverse=True)
        return results


if __name__ == "__main__":
    compiler = SemanticCompiler()

    print("=== Available Metrics ===")
    for m in compiler.list_metrics():
        print(f"  {m['name']}: {m['description']}")

    print("\n=== Available Segments ===")
    for s in compiler.list_segments():
        print(f"  {s['name']}: {s['description']}")

    print("\n=== Example Compilations ===\n")

    print("--- Annualized volatility of AAPL (2024) ---")
    q = compiler.compile("annualized_volatility", tickers=["AAPL"], start_date="2024-01-01", end_date="2024-12-31")
    print(q.sql)
    print(f"\nNotes: {q.notes}\n")

    print("--- Sharpe ratio for US equities (2024) ---")
    q = compiler.compile("sharpe_ratio", segment="us_equities", start_date="2024-01-01", end_date="2024-12-31")
    print(q.sql)
    print(f"\nNotes: {q.notes}\n")

    print("--- Beta of TSLA vs S&P 500 (2023-2024) ---")
    q = compiler.compile("beta", tickers=["TSLA"], start_date="2023-01-01", end_date="2024-12-31")
    print(q.sql)
    print(f"\nNotes: {q.notes}\n")

    print("--- Correlation AAPL vs MSFT (2024) ---")
    q = compiler.compile("correlation", tickers=["AAPL", "MSFT"], start_date="2024-01-01", end_date="2024-12-31")
    print(q.sql)
    print(f"\nNotes: {q.notes}\n")

    print("--- Max drawdown for Oslo Bors stocks (all time) ---")
    q = compiler.compile("max_drawdown", segment="oslo_bors")
    print(q.sql)
    print(f"\nNotes: {q.notes}\n")

    print("--- Lookup: 'What is the Sharpe ratio?' ---")
    results = compiler.lookup("What is the Sharpe ratio?")
    for r in results[:3]:
        print(f"  {r['name']} (score: {r['score']}): {r['description']}")
