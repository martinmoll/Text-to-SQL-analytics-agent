"""Tests for the response formatter: answer generation, provenance, markdown output."""

import pytest
import pandas as pd
from datetime import date

from agent.response_formatter import (
    ResponseFormatter,
    AgentResponse,
    Provenance,
    _format_value,
    _detect_currency,
    _detect_coverage,
)


@pytest.fixture
def formatter():
    return ResponseFormatter()


# ── _format_value ──────────────────────────────────────────────────────────

class TestFormatValue:
    def test_nan_shows_dash(self):
        assert _format_value(float("nan")) == "—"

    def test_none_shows_dash(self):
        assert _format_value(None) == "—"

    def test_small_float_shows_six_decimals(self):
        result = _format_value(0.005432)
        assert "0.005432" in result

    def test_medium_float_shows_four_decimals(self):
        result = _format_value(1.2345)
        assert "1.2345" in result

    def test_large_float_shows_two_decimals_with_comma(self):
        result = _format_value(12345.6789)
        assert "12,345.68" in result

    def test_zero_float(self):
        assert _format_value(0.0) == "0.0000"

    def test_date_value(self):
        assert _format_value(date(2024, 6, 15)) == "2024-06-15"

    def test_string_passthrough(self):
        assert _format_value("AAPL") == "AAPL"

    def test_integer_passthrough(self):
        assert _format_value(42) == "42"


# ── _detect_currency ───────────────────────────────────────────────────────

class TestDetectCurrency:
    def test_none_dataframe(self):
        assert _detect_currency(None, []) is None

    def test_explicit_currency_column_single(self):
        df = pd.DataFrame({"currency": ["USD", "USD"], "value": [1, 2]})
        assert _detect_currency(df, []) == "USD"

    def test_explicit_currency_column_mixed(self):
        df = pd.DataFrame({"currency": ["USD", "NOK"], "value": [1, 2]})
        result = _detect_currency(df, [])
        assert "Mixed" in result
        assert "USD" in result
        assert "NOK" in result

    def test_infer_from_oslo_tickers(self):
        df = pd.DataFrame({"ticker": ["EQNR.OL", "DNB.OL"]})
        assert _detect_currency(df, []) == "NOK"

    def test_infer_from_us_tickers(self):
        df = pd.DataFrame({"ticker": ["AAPL", "MSFT"]})
        assert _detect_currency(df, []) == "USD"

    def test_infer_mixed_tickers(self):
        df = pd.DataFrame({"ticker": ["AAPL", "EQNR.OL"]})
        assert _detect_currency(df, []) == "Mixed (USD, NOK)"

    def test_index_tickers_not_counted_as_us(self):
        df = pd.DataFrame({"ticker": ["^GSPC", "EQNR.OL"]})
        assert _detect_currency(df, []) == "NOK"


# ── _detect_coverage ───────────────────────────────────────────────────────

class TestDetectCoverage:
    def test_none_dataframe(self):
        assert _detect_coverage(None) is None

    def test_few_tickers_listed(self):
        df = pd.DataFrame({"ticker": ["AAPL", "MSFT", "GOOGL"]})
        result = _detect_coverage(df)
        assert "AAPL" in result
        assert "MSFT" in result

    def test_many_tickers_counted(self):
        df = pd.DataFrame({"ticker": [f"T{i}" for i in range(15)]})
        result = _detect_coverage(df)
        assert "15 tickers" in result

    def test_no_ticker_column_shows_rows(self):
        df = pd.DataFrame({"value": [1, 2, 3]})
        result = _detect_coverage(df)
        assert "3 rows" in result


# ── ResponseFormatter.format ───────────────────────────────────────────────

class TestFormatterFormat:
    def test_single_row_result(self, formatter):
        df = pd.DataFrame({"ticker": ["AAPL"], "value": [0.1523]})
        response = formatter.format(
            question="What is AAPL's return?",
            sql="SELECT ...",
            result_df=df,
            metric_source="semantic_layer",
            tables_used=["fact_daily_prices"],
            metric_name="cumulative_return",
            metric_description="Cumulative return over a date range",
        )
        assert isinstance(response, AgentResponse)
        assert "AAPL" in response.answer
        assert "0.1523" in response.answer

    def test_multi_row_result(self, formatter):
        df = pd.DataFrame({
            "ticker": ["AAPL", "MSFT", "GOOGL"],
            "value": [0.15, 0.20, 0.12],
        })
        response = formatter.format(
            question="Top returns?",
            sql="SELECT ...",
            result_df=df,
            metric_source="semantic_layer",
            tables_used=["fact_daily_prices"],
        )
        assert "3 ticker" in response.answer

    def test_empty_result(self, formatter):
        df = pd.DataFrame()
        response = formatter.format(
            question="test",
            sql="SELECT ...",
            result_df=df,
            metric_source="llm_generated",
            tables_used=[],
        )
        assert "No data" in response.answer

    def test_none_result(self, formatter):
        response = formatter.format(
            question="test",
            sql="SELECT ...",
            result_df=None,
            metric_source="llm_generated",
            tables_used=[],
        )
        assert "No data" in response.answer

    def test_methodology_semantic_layer(self, formatter):
        df = pd.DataFrame({"ticker": ["AAPL"], "value": [0.15]})
        response = formatter.format(
            question="test",
            sql="SELECT ...",
            result_df=df,
            metric_source="semantic_layer",
            tables_used=["fact_daily_prices"],
            metric_name="sharpe_ratio",
            metric_description="Annualized risk-adjusted return",
        )
        assert "semantic layer" in response.methodology
        assert "sharpe_ratio" in response.methodology

    def test_methodology_llm_generated(self, formatter):
        df = pd.DataFrame({"value": [42]})
        response = formatter.format(
            question="test",
            sql="SELECT ...",
            result_df=df,
            metric_source="llm_generated",
            tables_used=["fact_daily_prices"],
        )
        assert "LLM" in response.methodology

    def test_provenance_populated(self, formatter):
        df = pd.DataFrame({"ticker": ["AAPL"], "value": [0.15]})
        response = formatter.format(
            question="test",
            sql="SELECT ...",
            result_df=df,
            metric_source="semantic_layer",
            tables_used=["fact_daily_prices"],
            review_summary="All checks passed",
        )
        assert response.provenance is not None
        assert "fact_daily_prices" in response.provenance.source_tables
        assert response.provenance.review_summary == "All checks passed"

    def test_warnings_passed_through(self, formatter):
        df = pd.DataFrame({"value": [1]})
        response = formatter.format(
            question="test",
            sql="SELECT ...",
            result_df=df,
            metric_source="llm_generated",
            tables_used=[],
            warnings=["Currency mismatch"],
        )
        assert "Currency mismatch" in response.warnings


# ── AgentResponse.to_markdown ──────────────────────────────────────────────

class TestToMarkdown:
    def test_contains_answer_section(self):
        resp = AgentResponse(question="q", answer="42", methodology="m", sql="SELECT 1")
        md = resp.to_markdown()
        assert "## Answer" in md
        assert "42" in md

    def test_contains_sql_section(self):
        resp = AgentResponse(question="q", answer="a", methodology="m", sql="SELECT 1")
        md = resp.to_markdown()
        assert "```sql" in md
        assert "SELECT 1" in md

    def test_contains_results_table(self):
        df = pd.DataFrame({"ticker": ["AAPL"], "value": [0.15]})
        resp = AgentResponse(question="q", answer="a", methodology="m", sql="s", data=df)
        md = resp.to_markdown()
        assert "## Results" in md
        assert "AAPL" in md

    def test_contains_warnings(self):
        resp = AgentResponse(
            question="q", answer="a", methodology="m", sql="s",
            warnings=["Watch out"],
        )
        md = resp.to_markdown()
        assert "## Warnings" in md
        assert "Watch out" in md

    def test_contains_provenance(self):
        prov = Provenance(
            source_tables=["fact_daily_prices"],
            metric_source="semantic_layer",
            review_summary="OK",
        )
        resp = AgentResponse(
            question="q", answer="a", methodology="m", sql="s",
            provenance=prov,
        )
        md = resp.to_markdown()
        assert "Provenance" in md
        assert "fact_daily_prices" in md


# ── AgentResponse.to_dict ─────────────────────────────────────────────────

class TestToDict:
    def test_basic_structure(self):
        resp = AgentResponse(question="q", answer="a", methodology="m", sql="s")
        d = resp.to_dict()
        assert d["question"] == "q"
        assert d["answer"] == "a"
        assert d["sql"] == "s"

    def test_with_provenance(self):
        prov = Provenance(source_tables=["t1"], metric_source="semantic_layer")
        resp = AgentResponse(
            question="q", answer="a", methodology="m", sql="s",
            provenance=prov,
        )
        d = resp.to_dict()
        assert d["provenance"]["source_tables"] == ["t1"]
        assert d["provenance"]["metric_source"] == "semantic_layer"

    def test_with_dataframe(self):
        df = pd.DataFrame({"x": [1, 2]})
        resp = AgentResponse(question="q", answer="a", methodology="m", sql="s", data=df)
        d = resp.to_dict()
        assert d["data"] == [{"x": 1}, {"x": 2}]
