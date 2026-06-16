"""Tests for the SQL reviewer's deterministic static checks."""

import pytest
from agent.reviewer import SQLReviewer, ReviewIssue


@pytest.fixture
def reviewer():
    return SQLReviewer.__new__(SQLReviewer)


# ── Static check: missing security_type filter ─────────────────────────────

class TestMissingSecurityTypeFilter:
    def test_flags_stocks_query_without_filter(self, reviewer):
        sql = "SELECT ticker, AVG(volume) FROM fact_daily_prices fp GROUP BY ticker"
        issues = reviewer._static_checks(sql, "What are the top stocks by volume?")
        categories = [i.category for i in issues]
        assert "missing_filter" in categories

    def test_passes_stocks_query_with_filter(self, reviewer):
        sql = """
            SELECT fp.ticker, AVG(fp.volume)
            FROM fact_daily_prices fp
            JOIN dim_securities s ON fp.ticker = s.ticker
            WHERE s.security_type = 'equity'
            GROUP BY fp.ticker
        """
        issues = reviewer._static_checks(sql, "What are the top stocks by volume?")
        categories = [i.category for i in issues]
        assert "missing_filter" not in categories

    def test_no_flag_when_question_doesnt_mention_stocks(self, reviewer):
        sql = "SELECT ticker, AVG(volume) FROM fact_daily_prices fp GROUP BY ticker"
        issues = reviewer._static_checks(sql, "What is the average volume of AAPL?")
        categories = [i.category for i in issues]
        assert "missing_filter" not in categories

    @pytest.mark.parametrize("word", ["stocks", "equities", "equity", "stock"])
    def test_all_stock_keywords_trigger(self, reviewer, word):
        sql = "SELECT ticker FROM fact_daily_prices fp"
        issues = reviewer._static_checks(sql, f"Show me {word}")
        categories = [i.category for i in issues]
        assert "missing_filter" in categories


# ── Static check: NULL prices in LAST/FIRST ────────────────────────────────

class TestNullPricesCheck:
    def test_flags_last_without_null_check(self, reviewer):
        sql = """
            SELECT ticker,
                LAST(adjusted_close ORDER BY date) / FIRST(adjusted_close ORDER BY date) - 1
            FROM fact_daily_prices fp
            GROUP BY ticker
        """
        issues = reviewer._static_checks(sql, "cumulative return")
        categories = [i.category for i in issues]
        assert "null_prices" in categories

    def test_passes_last_with_null_check(self, reviewer):
        sql = """
            SELECT ticker,
                LAST(adjusted_close ORDER BY date) / FIRST(adjusted_close ORDER BY date) - 1
            FROM fact_daily_prices fp
            WHERE fp.adjusted_close IS NOT NULL
            GROUP BY ticker
        """
        issues = reviewer._static_checks(sql, "cumulative return")
        categories = [i.category for i in issues]
        assert "null_prices" not in categories

    def test_no_flag_without_last_first(self, reviewer):
        sql = "SELECT ticker, AVG(daily_log_return) FROM fact_daily_prices GROUP BY ticker"
        issues = reviewer._static_checks(sql, "average return")
        categories = [i.category for i in issues]
        assert "null_prices" not in categories


# ── Static check: calendar join missing exchange ───────────────────────────

class TestCalendarJoinCheck:
    def test_flags_calendar_join_without_exchange(self, reviewer):
        sql = """
            SELECT fp.ticker, dc.quarter
            FROM fact_daily_prices fp
            JOIN dim_calendar dc ON fp.date = dc.date
            GROUP BY fp.ticker, dc.quarter
        """
        issues = reviewer._static_checks(sql, "quarterly return")
        categories = [i.category for i in issues]
        assert "calendar_mismatch" in categories

    def test_passes_calendar_join_with_exchange(self, reviewer):
        sql = """
            SELECT fp.ticker, dc.quarter
            FROM fact_daily_prices fp
            JOIN dim_securities s ON fp.ticker = s.ticker
            JOIN dim_calendar dc ON fp.date = dc.date AND s.exchange = dc.exchange
            GROUP BY fp.ticker, dc.quarter
        """
        issues = reviewer._static_checks(sql, "quarterly return")
        categories = [i.category for i in issues]
        assert "calendar_mismatch" not in categories

    def test_no_flag_without_calendar(self, reviewer):
        sql = "SELECT ticker, AVG(daily_log_return) FROM fact_daily_prices GROUP BY ticker"
        issues = reviewer._static_checks(sql, "average return")
        categories = [i.category for i in issues]
        assert "calendar_mismatch" not in categories


# ── Static check: window inside aggregate ──────────────────────────────────

class TestWindowInAggregateCheck:
    def test_flags_window_in_aggregate(self, reviewer):
        sql = """
            SELECT ticker,
                MIN(adjusted_close OVER (PARTITION BY ticker ORDER BY date))
            FROM fact_daily_prices
            GROUP BY ticker
        """
        issues = reviewer._static_checks(sql, "drawdown")
        categories = [i.category for i in issues]
        assert "window_vs_aggregate" in categories

    def test_passes_window_in_cte(self, reviewer):
        sql = """
            WITH peaks AS (
                SELECT ticker, date, adjusted_close,
                    MAX(adjusted_close) OVER (PARTITION BY ticker ORDER BY date) AS peak
                FROM fact_daily_prices
            )
            SELECT ticker, MIN(adjusted_close / peak - 1)
            FROM peaks
            GROUP BY ticker
        """
        issues = reviewer._static_checks(sql, "drawdown")
        categories = [i.category for i in issues]
        assert "window_vs_aggregate" not in categories


# ── Static check: missing date filter ──────────────────────────────────────

class TestDateFilterCheck:
    def test_flags_missing_date_filter(self, reviewer):
        sql = "SELECT ticker, AVG(daily_log_return) FROM fact_daily_prices GROUP BY ticker"
        issues = reviewer._static_checks(sql, "What was AAPL's return in 2024?")
        categories = [i.category for i in issues]
        assert "date_range" in categories

    def test_passes_with_date_filter(self, reviewer):
        sql = """
            SELECT ticker, AVG(daily_log_return)
            FROM fact_daily_prices
            WHERE date >= '2024-01-01' AND date <= '2024-12-31'
            GROUP BY ticker
        """
        issues = reviewer._static_checks(sql, "What was AAPL's return in 2024?")
        categories = [i.category for i in issues]
        assert "date_range" not in categories

    def test_no_flag_when_question_has_no_period(self, reviewer):
        sql = "SELECT ticker, AVG(daily_log_return) FROM fact_daily_prices GROUP BY ticker"
        issues = reviewer._static_checks(sql, "What is the average return of AAPL?")
        categories = [i.category for i in issues]
        assert "date_range" not in categories


# ── Static summary ─────────────────────────────────────────────────────────

class TestStaticSummary:
    def test_no_issues_summary(self, reviewer):
        assert reviewer._static_summary([]) == "Static checks passed"

    def test_errors_summary(self, reviewer):
        issues = [ReviewIssue("test", "error", "msg")]
        summary = reviewer._static_summary(issues)
        assert "1 error" in summary

    def test_warnings_summary(self, reviewer):
        issues = [ReviewIssue("test", "warning", "msg")]
        summary = reviewer._static_summary(issues)
        assert "1 warning" in summary

    def test_mixed_summary(self, reviewer):
        issues = [
            ReviewIssue("a", "error", "msg1"),
            ReviewIssue("b", "warning", "msg2"),
            ReviewIssue("c", "warning", "msg3"),
        ]
        summary = reviewer._static_summary(issues)
        assert "1 error" in summary
        assert "2 warning" in summary


# ── LLM review parsing ────────────────────────────────────────────────────

class TestLLMReviewParsing:
    def test_parse_passed(self, reviewer):
        text = "PASSED\nSUMMARY: No issues found."
        result = reviewer._parse_llm_review(text)
        assert result.passed
        assert "No issues found" in result.summary

    def test_parse_issues(self, reviewer):
        text = """ISSUE: 2. MISSING FILTER
SEVERITY: error
MESSAGE: Query asks about stocks but no security_type filter
SUGGESTION: Add WHERE s.security_type = 'equity'

SUMMARY: Found one missing filter issue."""
        result = reviewer._parse_llm_review(text)
        assert not result.passed
        assert len(result.issues) >= 1
        assert result.issues[0].severity == "error"

    def test_parse_revised_sql(self, reviewer):
        text = """ISSUE: 9. NULL PRICES
SEVERITY: warning
MESSAGE: Missing null check
SUGGESTION: Add IS NOT NULL

SUMMARY: Minor null handling issue.
REVISED_SQL:
```sql
SELECT ticker FROM fact_daily_prices WHERE adjusted_close IS NOT NULL
```"""
        result = reviewer._parse_llm_review(text)
        assert result.revised_sql is not None
        assert "IS NOT NULL" in result.revised_sql
