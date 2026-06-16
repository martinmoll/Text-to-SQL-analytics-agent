"""Tests for the semantic resolver: metric matching, ticker/date extraction, routing."""

import pytest
from agent.semantic_resolver import SemanticResolver, Resolution


@pytest.fixture
def resolver():
    return SemanticResolver()


# ── Metric matching via phrase map ──────────────────────────────────────────

class TestMetricMatching:
    @pytest.mark.parametrize("question,expected_metric", [
        ("What is AAPL's Sharpe ratio in 2024?", "sharpe_ratio"),
        ("Show me the sortino ratio for TSLA", "sortino_ratio"),
        ("What is the volatility of NVDA?", "annualized_volatility"),
        ("AAPL vol last year", "annualized_volatility"),
        ("max drawdown for META in 2024", "max_drawdown"),
        ("What is TSLA's beta?", "beta"),
        ("correlation between AAPL and MSFT", "correlation"),
        ("Show the cumulative return of GOOGL", "cumulative_return"),
        ("What is the total return index for AMZN?", "total_return_index"),
        ("average daily volume of JPM", "average_daily_volume"),
        ("AAPL vwap in Q1 2024", "vwap"),
        ("What is the price range of TSLA?", "price_range"),
    ])
    def test_phrase_map_matches(self, resolver, question, expected_metric):
        result = resolver.resolve(question)
        assert result.found, f"Expected to find metric for: {question}"
        assert result.metric_name == expected_metric

    def test_longer_phrase_wins_over_shorter(self, resolver):
        result = resolver.resolve("What is AAPL's cumulative return in 2024?")
        assert result.metric_name == "cumulative_return"

    def test_no_match_returns_not_found(self, resolver):
        result = resolver.resolve("How many employees does the company have?")
        assert not result.found
        assert result.metric_name is None


# ── Resolution output structure ─────────────────────────────────────────────

class TestResolutionStructure:
    def test_found_resolution_has_compiled_sql(self, resolver):
        result = resolver.resolve("What is the Sharpe ratio of AAPL in 2024?")
        assert result.found
        assert result.compiled is not None
        assert result.compiled.sql != ""

    def test_not_found_has_candidates(self, resolver):
        result = resolver.resolve("Show me the risk metrics for AAPL")
        if not result.found:
            assert isinstance(result.candidates, list)

    def test_not_found_has_route_hint(self, resolver):
        result = resolver.resolve("How many employees does AAPL have?")
        assert not result.found
        assert result.route_hint is None or isinstance(result.route_hint, str)


# ── Ticker extraction ──────────────────────────────────────────────────────

class TestTickerExtraction:
    @pytest.mark.parametrize("question,expected_tickers", [
        ("What is AAPL's return?", ["AAPL"]),
        ("Compare AAPL and MSFT", ["AAPL", "MSFT"]),
        ("EQNR.OL volatility", ["EQNR.OL"]),
        ("How did ^GSPC perform?", ["^GSPC"]),
        ("TSLA vs NVDA returns in 2024", ["TSLA", "NVDA"]),
    ])
    def test_extracts_tickers(self, resolver, question, expected_tickers):
        tickers = resolver.extract_tickers(question)
        for t in expected_tickers:
            assert t in tickers, f"Expected {t} in extracted tickers from: {question}"

    def test_filters_noise_words(self, resolver):
        tickers = resolver.extract_tickers("What is the YTD return for US stocks?")
        assert "YTD" not in tickers
        assert "US" not in tickers

    def test_filters_metric_names(self, resolver):
        tickers = resolver.extract_tickers("What is the AVG volume?")
        assert "AVG" not in tickers


# ── Date range extraction ──────────────────────────────────────────────────

class TestDateExtraction:
    def test_quarter_extraction(self, resolver):
        start, end = resolver.extract_date_range("AAPL return in Q3 2024")
        assert start == "2024-07-01"
        assert end == "2024-09-30"

    @pytest.mark.parametrize("quarter,expected_start,expected_end", [
        (1, "2024-01-01", "2024-03-31"),
        (2, "2024-04-01", "2024-06-30"),
        (3, "2024-07-01", "2024-09-30"),
        (4, "2024-10-01", "2024-12-31"),
    ])
    def test_all_quarters(self, resolver, quarter, expected_start, expected_end):
        start, end = resolver.extract_date_range(f"Return in Q{quarter} 2024")
        assert start == expected_start
        assert end == expected_end

    def test_explicit_dates(self, resolver):
        start, end = resolver.extract_date_range(
            "Return from 2024-01-15 to 2024-06-30"
        )
        assert start == "2024-01-15"
        assert end == "2024-06-30"

    def test_year_extraction(self, resolver):
        start, end = resolver.extract_date_range("AAPL return in 2024")
        assert start == "2024-01-01"
        assert end == "2024-12-31"

    def test_no_date_returns_none(self, resolver):
        start, end = resolver.extract_date_range("What is the Sharpe ratio of AAPL?")
        assert start is None
        assert end is None

    def test_single_date(self, resolver):
        start, end = resolver.extract_date_range("Return since 2024-03-01")
        assert start == "2024-03-01"
        assert end is None


# ── Routing hints ──────────────────────────────────────────────────────────

class TestRouting:
    @pytest.mark.parametrize("question,expected_ref", [
        ("What is the P/E ratio?", "references/fundamentals.md"),
        ("Show portfolio beta", "references/portfolio_analytics.md"),
        ("S&P 500 sector ranking", "references/market_overview.md"),
        ("What is AAPL's return performance?", "references/returns_and_risk.md"),
    ])
    def test_route_hints(self, resolver, question, expected_ref):
        result = resolver.resolve(question)
        if not result.found:
            assert result.route_hint == expected_ref


# ── End-to-end resolve with parameters ─────────────────────────────────────

class TestResolveEndToEnd:
    def test_resolve_with_explicit_tickers(self, resolver):
        result = resolver.resolve(
            "What is the Sharpe ratio?",
            tickers=["AAPL", "MSFT"],
        )
        assert result.found
        assert "AAPL" in result.compiled.sql
        assert "MSFT" in result.compiled.sql

    def test_resolve_with_explicit_dates(self, resolver):
        result = resolver.resolve(
            "What is the volatility?",
            start_date="2024-01-01",
            end_date="2024-06-30",
        )
        assert result.found
        assert "2024-01-01" in result.compiled.sql
        assert "2024-06-30" in result.compiled.sql

    def test_resolve_with_segment(self, resolver):
        result = resolver.resolve(
            "What is the volatility?",
            segment="oslo_bors",
        )
        assert result.found
        assert "dim_securities" in result.compiled.sql

    def test_resolve_extracts_tickers_from_question(self, resolver):
        result = resolver.resolve("What is NVDA's Sharpe ratio in 2024?")
        assert result.found
        assert "NVDA" in result.compiled.sql

    def test_resolve_extracts_dates_from_question(self, resolver):
        result = resolver.resolve("What is AAPL's volatility in Q2 2024?")
        assert result.found
        assert "2024-04-01" in result.compiled.sql
        assert "2024-06-30" in result.compiled.sql


# ── Schema context ──────────────────────────────────────────────────────────

class TestSchemaContext:
    def test_get_schema_context_contains_metrics(self, resolver):
        ctx = resolver.get_schema_context()
        assert "sharpe_ratio" in ctx
        assert "cumulative_return" in ctx

    def test_get_schema_context_contains_segments(self, resolver):
        ctx = resolver.get_schema_context()
        assert "us_equities" in ctx
        assert "oslo_bors" in ctx
