"""Tests for the semantic layer compiler: YAML definitions → DuckDB SQL."""

import pytest
from semantic_layer.compiler import SemanticCompiler, CompiledQuery


@pytest.fixture
def compiler():
    return SemanticCompiler()


# ── Initialization ──────────────────────────────────────────────────────────

class TestCompilerInit:
    def test_loads_metrics(self, compiler):
        assert len(compiler.metrics) > 0

    def test_loads_segments(self, compiler):
        assert len(compiler.segments) > 0

    def test_loads_dimensions(self, compiler):
        assert len(compiler.dimensions) > 0

    def test_all_expected_metrics_present(self, compiler):
        expected = [
            "daily_simple_return", "daily_log_return", "cumulative_return",
            "total_return_index", "annualized_volatility", "sharpe_ratio",
            "sortino_ratio", "max_drawdown", "beta", "correlation",
            "average_daily_volume", "price_range", "vwap",
        ]
        for name in expected:
            assert name in compiler.metrics, f"Missing metric: {name}"


# ── list_metrics / list_segments ────────────────────────────────────────────

class TestListMethods:
    def test_list_metrics_returns_dicts(self, compiler):
        metrics = compiler.list_metrics()
        assert isinstance(metrics, list)
        assert all(isinstance(m, dict) for m in metrics)
        assert all("name" in m and "description" in m for m in metrics)

    def test_list_segments_returns_dicts(self, compiler):
        segments = compiler.list_segments()
        assert isinstance(segments, list)
        assert all("name" in s and "filter" in s for s in segments)

    def test_get_metric_existing(self, compiler):
        m = compiler.get_metric("sharpe_ratio")
        assert m is not None
        assert "expression" in m

    def test_get_metric_missing(self, compiler):
        assert compiler.get_metric("nonexistent_metric") is None

    def test_get_segment_filter(self, compiler):
        f = compiler.get_segment_filter("us_equities")
        assert f is not None
        assert "equity" in f.lower()

    def test_get_segment_filter_missing(self, compiler):
        assert compiler.get_segment_filter("nonexistent_segment") is None


# ── compile: pre-computed column metrics ────────────────────────────────────

class TestCompileColumnMetrics:
    def test_daily_simple_return(self, compiler):
        result = compiler.compile(
            "daily_simple_return",
            tickers=["AAPL"],
            start_date="2024-01-01",
            end_date="2024-12-31",
        )
        assert isinstance(result, CompiledQuery)
        assert "daily_simple_return" in result.sql
        assert "AAPL" in result.sql
        assert "2024-01-01" in result.sql
        assert "ORDER BY" in result.sql

    def test_daily_log_return(self, compiler):
        result = compiler.compile("daily_log_return", tickers=["MSFT"])
        assert "daily_log_return" in result.sql
        assert "MSFT" in result.sql

    def test_column_query_has_date_in_select(self, compiler):
        result = compiler.compile("daily_simple_return", tickers=["AAPL"])
        assert "fp.date" in result.sql


# ── compile: aggregate metrics ──────────────────────────────────────────────

class TestCompileAggregateMetrics:
    def test_cumulative_return(self, compiler):
        result = compiler.compile(
            "cumulative_return",
            tickers=["AAPL"],
            start_date="2024-01-01",
            end_date="2024-12-31",
        )
        assert "LAST(" in result.sql
        assert "FIRST(" in result.sql
        assert "GROUP BY" in result.sql

    def test_annualized_volatility(self, compiler):
        result = compiler.compile(
            "annualized_volatility",
            tickers=["TSLA"],
            start_date="2024-01-01",
            end_date="2024-12-31",
        )
        assert "STDDEV" in result.sql
        assert "SQRT(252)" in result.sql
        assert "TSLA" in result.sql

    def test_average_daily_volume(self, compiler):
        result = compiler.compile(
            "average_daily_volume",
            tickers=["NVDA"],
            start_date="2024-01-01",
            end_date="2024-06-30",
        )
        assert "AVG(volume)" in result.sql

    def test_vwap(self, compiler):
        result = compiler.compile("vwap", tickers=["AAPL"])
        assert "SUM(adjusted_close * volume)" in result.sql
        assert "NULLIF" in result.sql

    def test_price_range(self, compiler):
        result = compiler.compile("price_range", tickers=["GOOGL"])
        assert "MAX(high)" in result.sql
        assert "MIN(low)" in result.sql


# ── compile: parameterized metrics ──────────────────────────────────────────

class TestCompileParameterizedMetrics:
    def test_sharpe_ratio_default_rfr(self, compiler):
        result = compiler.compile(
            "sharpe_ratio",
            tickers=["AAPL"],
            start_date="2024-01-01",
            end_date="2024-12-31",
        )
        assert "0.05" in result.sql
        assert result.parameters_used["risk_free_rate"] == 0.05

    def test_sharpe_ratio_custom_rfr(self, compiler):
        result = compiler.compile(
            "sharpe_ratio",
            tickers=["AAPL"],
            start_date="2024-01-01",
            end_date="2024-12-31",
            parameters={"risk_free_rate": 0.03},
        )
        assert "0.03" in result.sql
        assert result.parameters_used["risk_free_rate"] == 0.03

    def test_sortino_ratio(self, compiler):
        result = compiler.compile(
            "sortino_ratio",
            tickers=["META"],
            start_date="2024-01-01",
            end_date="2024-12-31",
        )
        assert "CASE WHEN" in result.sql
        assert "daily_log_return < 0" in result.sql


# ── compile: CTE metrics ───────────────────────────────────────────────────

class TestCompileCTEMetrics:
    def test_max_drawdown(self, compiler):
        result = compiler.compile(
            "max_drawdown",
            tickers=["AAPL"],
            start_date="2024-01-01",
            end_date="2024-12-31",
        )
        assert "WITH running_max" in result.sql
        assert "MIN(adjusted_close / peak - 1)" in result.sql
        assert "AAPL" in result.sql

    def test_max_drawdown_with_segment(self, compiler):
        result = compiler.compile("max_drawdown", segment="oslo_bors")
        assert "dim_securities" in result.sql
        assert "OSE" in result.sql


# ── compile: join-based metrics ─────────────────────────────────────────────

class TestCompileJoinMetrics:
    def test_beta_default_benchmark(self, compiler):
        result = compiler.compile(
            "beta",
            tickers=["TSLA"],
            start_date="2023-01-01",
            end_date="2024-12-31",
        )
        assert "REGR_SLOPE" in result.sql
        assert "REGR_R2" in result.sql
        assert "^GSPC" in result.sql
        assert "TSLA" in result.sql

    def test_beta_custom_benchmark(self, compiler):
        result = compiler.compile(
            "beta",
            tickers=["EQNR.OL"],
            start_date="2024-01-01",
            end_date="2024-12-31",
            parameters={"market_benchmark": "^IXIC"},
        )
        assert "^IXIC" in result.sql

    def test_correlation_two_tickers(self, compiler):
        result = compiler.compile(
            "correlation",
            tickers=["AAPL", "MSFT"],
            start_date="2024-01-01",
            end_date="2024-12-31",
        )
        assert "CORR(" in result.sql
        assert "AAPL" in result.sql
        assert "MSFT" in result.sql

    def test_correlation_requires_two_tickers(self, compiler):
        with pytest.raises(ValueError, match="at least 2 tickers"):
            compiler.compile("correlation", tickers=["AAPL"])


# ── compile: WHERE clause construction ──────────────────────────────────────

class TestCompileWhereClauses:
    def test_ticker_filter(self, compiler):
        result = compiler.compile("cumulative_return", tickers=["AAPL", "MSFT"])
        assert "'AAPL'" in result.sql
        assert "'MSFT'" in result.sql
        assert "fp.ticker IN" in result.sql

    def test_date_range_filter(self, compiler):
        result = compiler.compile(
            "cumulative_return",
            tickers=["AAPL"],
            start_date="2024-01-01",
            end_date="2024-12-31",
        )
        assert "fp.date >= '2024-01-01'" in result.sql
        assert "fp.date <= '2024-12-31'" in result.sql

    def test_segment_filter_adds_join(self, compiler):
        result = compiler.compile("annualized_volatility", segment="tech_stocks")
        assert "dim_securities" in result.sql
        assert "Technology" in result.sql

    def test_no_filters_produces_no_where(self, compiler):
        result = compiler.compile("daily_simple_return")
        assert "WHERE" not in result.sql


# ── compile: error handling ─────────────────────────────────────────────────

class TestCompileErrors:
    def test_unknown_metric_raises(self, compiler):
        with pytest.raises(ValueError, match="Unknown metric"):
            compiler.compile("nonexistent_metric")

    def test_unknown_metric_lists_available(self, compiler):
        with pytest.raises(ValueError, match="sharpe_ratio"):
            compiler.compile("nonexistent_metric")


# ── compile: CompiledQuery attributes ──────────────────────────────────────

class TestCompiledQueryAttributes:
    def test_has_metric_name(self, compiler):
        result = compiler.compile("sharpe_ratio", tickers=["AAPL"])
        assert result.metric_name == "sharpe_ratio"

    def test_has_description(self, compiler):
        result = compiler.compile("sharpe_ratio", tickers=["AAPL"])
        assert "excess return" in result.description.lower() or "sharpe" in result.description.lower()

    def test_has_notes(self, compiler):
        result = compiler.compile("sharpe_ratio", tickers=["AAPL"])
        assert result.notes is not None

    def test_parameters_used_populated(self, compiler):
        result = compiler.compile("sharpe_ratio", tickers=["AAPL"])
        assert "risk_free_rate" in result.parameters_used


# ── lookup ──────────────────────────────────────────────────────────────────

class TestLookup:
    def test_lookup_returns_matches(self, compiler):
        results = compiler.lookup("What is the Sharpe ratio?")
        assert len(results) > 0
        assert results[0]["name"] == "sharpe_ratio"

    def test_lookup_sorted_by_score(self, compiler):
        results = compiler.lookup("volatility of returns")
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_lookup_no_match(self, compiler):
        results = compiler.lookup("xyzzy foobar gibberish")
        assert len(results) == 0

    def test_lookup_result_structure(self, compiler):
        results = compiler.lookup("beta")
        assert all("name" in r and "description" in r and "score" in r for r in results)
