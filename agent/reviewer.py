"""
Adversarial SQL reviewer: checks generated SQL for the 9 common error
types documented in the analyst skill (Step 5).

Runs both deterministic pattern checks and an LLM-based semantic review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import anthropic


@dataclass
class ReviewIssue:
    category: str
    severity: str  # "error" | "warning"
    message: str
    suggestion: str | None = None


@dataclass
class ReviewResult:
    passed: bool
    issues: list[ReviewIssue] = field(default_factory=list)
    revised_sql: str | None = None
    summary: str = ""


_REVIEW_SYSTEM_PROMPT = """\
You are an adversarial SQL reviewer for a financial analytics agent. Your job \
is to find bugs in DuckDB SQL queries against a financial data warehouse.

## Database Schema (abbreviated)

fact_daily_prices: ticker, date, open, high, low, close, adjusted_close, \
volume, daily_simple_return, daily_log_return
dim_securities: ticker, short_name, sector, industry, exchange, currency, \
market_cap, country, security_type ('equity' or 'index')
dim_calendar: date, exchange, year, quarter, month, day_of_week, is_month_end

## Error Checklist
Review the SQL for EACH of these error types:

1. WRONG GRAIN — Does GROUP BY match the expected output? One row per ticker? \
Per ticker-date? Per sector?
2. MISSING FILTER — Should security_type = 'equity' be present? Are indices \
leaking into a stocks-only query?
3. DATE RANGE — Is the date filter correct? Q1 2024 = 2024-01-01 to \
2024-03-31. YTD starts Jan 1.
4. CURRENCY MIXING — Are price-denominated values being compared across USD \
and NOK without noting it?
5. CALENDAR MISMATCH — If dim_calendar is joined, is the join on BOTH date \
AND exchange?
6. NULL HANDLING — Is NULLIF used to avoid division by zero? Are NULL \
first-day returns handled?
7. INDEX CONFUSION — Are index tickers (^GSPC, ^OEX, ^IXIC) included when \
they shouldn't be?
8. WINDOW VS AGGREGATE — Is a window function used inside an aggregate? \
(Needs a CTE.)
9. NULL PRICES — When using LAST/FIRST ordered aggregates, is there a \
filter for adjusted_close IS NOT NULL? (Oslo Bors partial day issue.)

## Output Format
For each issue found, output:
ISSUE: <category number>. <category name>
SEVERITY: error | warning
MESSAGE: <what's wrong>
SUGGESTION: <how to fix>

If no issues found, output: PASSED

Then output:
SUMMARY: <one-line summary of the review>

If you found issues and can fix them, also output:
REVISED_SQL:
```sql
<fixed query>
```
"""


class SQLReviewer:
    def __init__(self, api_key: str | None = None, model: str = "claude-sonnet-4-6") -> None:
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def review(self, sql: str, question: str, source: str = "unknown") -> ReviewResult:
        """Review SQL for common errors using both static and LLM checks."""
        static_issues = self._static_checks(sql, question)

        llm_result = self._llm_review(sql, question, source)

        all_issues = static_issues + llm_result.issues
        has_errors = any(i.severity == "error" for i in all_issues)

        return ReviewResult(
            passed=not has_errors,
            issues=all_issues,
            revised_sql=llm_result.revised_sql,
            summary=llm_result.summary or self._static_summary(static_issues),
        )

    def _static_checks(self, sql: str, question: str) -> list[ReviewIssue]:
        issues: list[ReviewIssue] = []
        sql_upper = sql.upper()
        q_lower = question.lower()

        stocks_words = {"stocks", "equities", "equity", "stock"}
        asks_about_stocks = any(w in q_lower for w in stocks_words)
        if asks_about_stocks and "SECURITY_TYPE" not in sql_upper:
            issues.append(ReviewIssue(
                category="missing_filter",
                severity="error",
                message="Question asks about stocks but SQL has no security_type = 'equity' filter",
                suggestion="Add WHERE s.security_type = 'equity' or equivalent filter",
            ))

        has_last_first = "LAST(" in sql_upper or "FIRST(" in sql_upper
        has_null_check = "ADJUSTED_CLOSE IS NOT NULL" in sql_upper
        if has_last_first and not has_null_check:
            issues.append(ReviewIssue(
                category="null_prices",
                severity="warning",
                message="LAST/FIRST aggregate used without IS NOT NULL filter on adjusted_close",
                suggestion="Add AND fp.adjusted_close IS NOT NULL to avoid NULLs from partial trading days",
            ))

        if "DIM_CALENDAR" in sql_upper:
            join_pattern = re.search(
                r"JOIN\s+DIM_CALENDAR.*?ON\s+(.*?)(?:WHERE|GROUP|ORDER|$)",
                sql_upper, re.DOTALL,
            )
            if join_pattern:
                join_clause = join_pattern.group(1)
                if "EXCHANGE" not in join_clause:
                    issues.append(ReviewIssue(
                        category="calendar_mismatch",
                        severity="error",
                        message="dim_calendar joined on date only, missing exchange",
                        suggestion="Join dim_calendar on BOTH date AND exchange",
                    ))

        window_in_agg = re.search(
            r"\b(MIN|MAX|AVG|SUM|COUNT)\s*\([^)]*\bOVER\s*\(",
            sql_upper,
        )
        if window_in_agg:
            issues.append(ReviewIssue(
                category="window_vs_aggregate",
                severity="error",
                message="Window function used inside an aggregate function",
                suggestion="Extract the window function into a CTE first",
            ))

        has_date_filter = re.search(
            r"(DATE\s*(>=|<=|BETWEEN|>|<)|\.DATE\s*(>=|<=|BETWEEN|>|<))",
            sql_upper,
        )
        asks_about_period = any(w in q_lower for w in [
            "q1", "q2", "q3", "q4", "quarter", "year", "month",
            "2024", "2023", "2022", "ytd", "last",
        ])
        if asks_about_period and not has_date_filter and "ALL" not in sql_upper:
            issues.append(ReviewIssue(
                category="date_range",
                severity="warning",
                message="Question references a time period but SQL has no date filter",
                suggestion="Add a date range filter matching the requested period",
            ))

        return issues

    def _llm_review(self, sql: str, question: str, source: str) -> ReviewResult:
        user_prompt = (
            f"## Original Question\n{question}\n\n"
            f"## SQL Source\n{source}\n\n"
            f"## SQL to Review\n```sql\n{sql}\n```\n\n"
            "Review this SQL against the error checklist. Be thorough but "
            "avoid false positives — only flag genuine issues."
        )

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                system=_REVIEW_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return self._parse_llm_review(response.content[0].text)
        except Exception as e:
            return ReviewResult(
                passed=True,
                summary=f"LLM review unavailable; static checks only. Reason: {e}",
            )

    def _parse_llm_review(self, text: str) -> ReviewResult:
        issues: list[ReviewIssue] = []
        revised_sql: str | None = None
        summary = ""

        if "PASSED" in text.upper().split("\n")[0] if text.strip() else False:
            summary_match = re.search(r"SUMMARY:\s*(.+)", text, re.IGNORECASE)
            return ReviewResult(
                passed=True,
                summary=summary_match.group(1).strip() if summary_match else "No issues found",
            )

        issue_blocks = re.findall(
            r"ISSUE:\s*(.+?)(?=ISSUE:|SUMMARY:|REVISED_SQL:|$)",
            text, re.DOTALL | re.IGNORECASE,
        )

        for block in issue_blocks:
            category = ""
            severity = "warning"
            message = ""
            suggestion = ""

            cat_match = re.search(r"^(.+?)$", block.strip(), re.MULTILINE)
            if cat_match:
                category = cat_match.group(1).strip().lower()

            sev_match = re.search(r"SEVERITY:\s*(\w+)", block, re.IGNORECASE)
            if sev_match:
                severity = sev_match.group(1).strip().lower()

            msg_match = re.search(r"MESSAGE:\s*(.+?)(?=SEVERITY:|SUGGESTION:|$)", block, re.DOTALL | re.IGNORECASE)
            if msg_match:
                message = msg_match.group(1).strip()

            sug_match = re.search(r"SUGGESTION:\s*(.+?)$", block, re.DOTALL | re.IGNORECASE)
            if sug_match:
                suggestion = sug_match.group(1).strip()

            if message or category:
                issues.append(ReviewIssue(
                    category=category,
                    severity=severity,
                    message=message,
                    suggestion=suggestion or None,
                ))

        sql_match = re.search(r"```sql\s*(.+?)```", text, re.DOTALL)
        if sql_match:
            revised_sql = sql_match.group(1).strip()

        summary_match = re.search(r"SUMMARY:\s*(.+?)(?=REVISED_SQL:|$)", text, re.DOTALL | re.IGNORECASE)
        if summary_match:
            summary = summary_match.group(1).strip()

        has_errors = any(i.severity == "error" for i in issues)
        return ReviewResult(
            passed=not has_errors,
            issues=issues,
            revised_sql=revised_sql,
            summary=summary,
        )

    def _static_summary(self, issues: list[ReviewIssue]) -> str:
        if not issues:
            return "Static checks passed"
        errors = sum(1 for i in issues if i.severity == "error")
        warnings = sum(1 for i in issues if i.severity == "warning")
        parts = []
        if errors:
            parts.append(f"{errors} error(s)")
        if warnings:
            parts.append(f"{warnings} warning(s)")
        return f"Static checks found {', '.join(parts)}"
