"""
Semantic resolver: checks the governed semantic layer for metric coverage
before falling back to LLM-generated SQL.

Wraps the SemanticCompiler to provide a clean interface for the orchestrator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from semantic_layer.compiler import SemanticCompiler, CompiledQuery


@dataclass
class Resolution:
    found: bool
    metric_name: str | None = None
    compiled: CompiledQuery | None = None
    candidates: list[dict] = field(default_factory=list)
    route_hint: str | None = None


# Keyword → metric mapping for common natural-language phrases
_PHRASE_MAP: dict[str, str] = {
    "sharpe": "sharpe_ratio",
    "sortino": "sortino_ratio",
    "volatility": "annualized_volatility",
    "vol": "annualized_volatility",
    "drawdown": "max_drawdown",
    "mdd": "max_drawdown",
    "beta": "beta",
    "correlation": "correlation",
    "corr": "correlation",
    "cumulative return": "cumulative_return",
    "total return": "total_return_index",
    "simple return": "daily_simple_return",
    "log return": "daily_log_return",
    "volume": "average_daily_volume",
    "vwap": "vwap",
    "price range": "price_range",
}

# Domain routing hints (when semantic layer has no coverage)
_ROUTE_KEYWORDS: dict[str, list[str]] = {
    "references/returns_and_risk.md": [
        "return", "volatility", "sharpe", "sortino", "drawdown",
        "var", "risk", "performance",
    ],
    "references/fundamentals.md": [
        "p/e", "pe ratio", "price-to-earnings", "earnings", "revenue",
        "margin", "ebitda", "dividend", "book value", "balance sheet",
        "fundamental", "valuation", "profit", "income", "debt", "peg",
    ],
    "references/portfolio_analytics.md": [
        "correlation", "beta", "portfolio", "diversif", "allocation",
        "weight", "relative performance", "excess return",
    ],
    "references/market_overview.md": [
        "index", "sector", "market", "breadth", "ranking",
        "best performing", "worst performing", "s&p", "nasdaq",
        "overview", "heatmap",
    ],
}


_TICKER_PATTERN = re.compile(
    r"(?<!\w)(\^[A-Z]{2,6}|[A-Z]{1,5}\.[A-Z]{1,2}|[A-Z]{1,5})(?!\w)"
)

_DATE_PATTERN = re.compile(
    r"\b(\d{4}-\d{2}-\d{2})\b"
)

_QUARTER_PATTERN = re.compile(
    r"\bQ([1-4])\s+(\d{4})\b", re.IGNORECASE,
)

_YEAR_PATTERN = re.compile(
    r"\b(20\d{2})\b"
)

_PERIOD_KEYWORDS = {
    "ytd": "year_to_date",
    "year to date": "year_to_date",
    "last year": "last_year",
    "last quarter": "last_quarter",
    "last month": "last_month",
}


def _quarter_to_dates(q: int, year: int) -> tuple[str, str]:
    starts = {1: "01-01", 2: "04-01", 3: "07-01", 4: "10-01"}
    ends = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}
    return f"{year}-{starts[q]}", f"{year}-{ends[q]}"


class SemanticResolver:
    def __init__(self) -> None:
        self.compiler = SemanticCompiler()

    def resolve(
        self,
        question: str,
        tickers: list[str] | None = None,
        start_date: date | str | None = None,
        end_date: date | str | None = None,
        segment: str | None = None,
        parameters: dict | None = None,
    ) -> Resolution:
        """Attempt to resolve a question through the semantic layer.

        Returns a Resolution indicating whether a governed metric was found,
        the compiled SQL if so, and routing hints if not.
        """
        if tickers is None:
            tickers = self.extract_tickers(question)
        if start_date is None and end_date is None:
            start_date, end_date = self.extract_date_range(question)

        metric_name = self._match_metric(question)

        if metric_name:
            try:
                compiled = self.compiler.compile(
                    metric_name,
                    tickers=tickers or None,
                    start_date=start_date,
                    end_date=end_date,
                    segment=segment,
                    parameters=parameters,
                )
                return Resolution(
                    found=True,
                    metric_name=metric_name,
                    compiled=compiled,
                    candidates=self.compiler.lookup(question)[:3],
                )
            except ValueError:
                pass

        candidates = self.compiler.lookup(question)
        route = self._suggest_route(question)
        return Resolution(
            found=False,
            candidates=candidates[:5],
            route_hint=route,
        )

    def _match_metric(self, question: str) -> str | None:
        q = question.lower()

        for phrase, metric in sorted(
            _PHRASE_MAP.items(), key=lambda x: -len(x[0])
        ):
            if phrase in q:
                return metric

        candidates = self.compiler.lookup(question)
        if candidates and candidates[0]["score"] >= 2:
            return candidates[0]["name"]

        return None

    def _suggest_route(self, question: str) -> str | None:
        q = question.lower()
        scores: dict[str, int] = {}
        for ref, keywords in _ROUTE_KEYWORDS.items():
            scores[ref] = sum(1 for kw in keywords if kw in q)
        best = max(scores, key=scores.get)  # type: ignore[arg-type]
        return best if scores[best] > 0 else None

    def extract_tickers(self, question: str) -> list[str]:
        known_tickers = {m["name"] for m in self.compiler.list_metrics()}
        noise = {"Q1", "Q2", "Q3", "Q4", "YTD", "US", "USD", "NOK", "CEO",
                 "IPO", "ETF", "PE", "EV", "OLS", "CAPM", "AVG", "MAX", "MIN",
                 "THE", "AND", "FOR", "NOT", "ALL", "TOP", "VS", "IN"}
        raw = _TICKER_PATTERN.findall(question)
        return [
            t for t in raw
            if t not in known_tickers and t not in noise
        ]

    def extract_date_range(self, question: str) -> tuple[str | None, str | None]:
        q_match = _QUARTER_PATTERN.search(question)
        if q_match:
            quarter, year = int(q_match.group(1)), int(q_match.group(2))
            return _quarter_to_dates(quarter, year)

        dates = _DATE_PATTERN.findall(question)
        if len(dates) >= 2:
            return dates[0], dates[1]
        if len(dates) == 1:
            return dates[0], None

        year_matches = _YEAR_PATTERN.findall(question)
        if year_matches:
            year = year_matches[-1]
            return f"{year}-01-01", f"{year}-12-31"

        return None, None

    def list_metrics(self) -> list[dict]:
        return self.compiler.list_metrics()

    def list_segments(self) -> list[dict]:
        return self.compiler.list_segments()

    def get_schema_context(self) -> str:
        """Return a concise schema description for LLM context."""
        lines = ["## Available Governed Metrics\n"]
        for m in self.compiler.list_metrics():
            lines.append(f"- **{m['name']}**: {m['description']} (table: {m['table']}, unit: {m['unit']})")
        lines.append("\n## Available Segments\n")
        for s in self.compiler.list_segments():
            lines.append(f"- **{s['name']}**: {s['description']} → `{s['filter']}`")
        return "\n".join(lines)
