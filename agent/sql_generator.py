"""
Skill-guided SQL generator: uses Claude to produce DuckDB SQL
when the semantic layer has no governed metric coverage.

Loads the appropriate reference doc (via knowledge skill routing)
and constructs a rich prompt with schema context, the analyst playbook,
and domain-specific gotchas.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import anthropic


SKILLS_DIR = Path(__file__).parent.parent / "skills"
REFERENCES_DIR = SKILLS_DIR / "references"


@dataclass
class GeneratedSQL:
    sql: str
    reasoning: str
    reference_used: str | None
    tables_used: list[str]
    warnings: list[str]
    declined: bool = False  # generator output NONE: required data not in warehouse


_SYSTEM_PROMPT = """\
You are a financial analytics SQL agent. You generate DuckDB SQL queries \
against a financial data warehouse.

## Rules
1. Write ONLY DuckDB-compatible SQL.
2. Always use table aliases: fp for fact_daily_prices, s for dim_securities, \
dc for dim_calendar.
3. Always filter by date range unless the user explicitly asks for all-time.
4. When the user says "stocks", filter s.security_type = 'equity' to exclude indices.
5. Use adjusted_close (NOT close) for any return or price-based calculations.
6. daily_simple_return and daily_log_return are NULL for the first trading day \
per ticker — this is expected.
7. For Oslo Bors tickers, adjusted_close can be NULL on partial trading days. \
Always add AND fp.adjusted_close IS NOT NULL when using FIRST/LAST ordered aggregates.
8. When comparing across exchanges, always note the currency difference \
(USD vs NOK).
9. Use REGR_SLOPE / REGR_R2 / CORR for regression and correlation.
10. Annualize with 252 trading days: volatility = STDDEV * SQRT(252), \
return = AVG * 252.
11. The warehouse contains ONLY price/volume data and security metadata. \
There is NO fundamentals data: no earnings, revenue, margins, P/E, \
price-to-book, book value, EPS, dividends, or balance sheet items. If the \
question requires fundamentals data, do NOT invent a query. Still produce \
all output sections, but write the literal text NONE under SQL: (instead of \
a sql code block), and explain in REASONING that fundamentals data is not \
available in the warehouse and which price-based metrics you could offer \
instead.

## Database Schema

### fact_daily_prices (grain: ticker × date)
Columns: ticker (VARCHAR), date (DATE), open (DOUBLE), high (DOUBLE), \
low (DOUBLE), close (DOUBLE), adjusted_close (DOUBLE), volume (BIGINT), \
daily_simple_return (DOUBLE), daily_log_return (DOUBLE)

### dim_securities (grain: ticker)
Columns: ticker (VARCHAR), short_name (VARCHAR), sector (VARCHAR), \
industry (VARCHAR), exchange (VARCHAR), currency (VARCHAR), \
market_cap (DOUBLE), country (VARCHAR), security_type (VARCHAR: \
'equity' or 'index')

### dim_calendar (grain: date × exchange)
Columns: date (DATE), exchange (VARCHAR), year (INT), quarter (INT), \
month (INT), day_of_week (INT), is_month_end (BOOLEAN)

## Available Tickers
US equities: AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA, JPM, V, JNJ, \
PG, UNH, HD, MA, PFE
Oslo Bors: EQNR.OL, DNB.OL, MOWI.OL, ORK.OL, TEL.OL, AKRBP.OL
Indices: ^GSPC (S&P 500), ^OEX (S&P 100), ^IXIC (NASDAQ Composite)
"""


def _load_skill(name: str) -> str:
    path = SKILLS_DIR / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _load_reference(ref_path: str) -> str:
    path = SKILLS_DIR / ref_path
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


class SQLGenerator:
    def __init__(self, api_key: str | None = None, model: str = "claude-sonnet-4-6") -> None:
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.analyst_skill = _load_skill("analyst.md")
        self.knowledge_skill = _load_skill("knowledge.md")

    def generate(
        self,
        question: str,
        route_hint: str | None = None,
        schema_context: str | None = None,
        semantic_candidates: list[dict] | None = None,
    ) -> GeneratedSQL:
        """Generate SQL for a question the semantic layer couldn't fully resolve."""

        reference_content = ""
        reference_used = route_hint
        if route_hint:
            reference_content = _load_reference(route_hint)

        user_prompt = self._build_user_prompt(
            question, reference_content, schema_context, semantic_candidates
        )

        response = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        return self._parse_response(response.content[0].text, reference_used)

    def _build_user_prompt(
        self,
        question: str,
        reference_content: str,
        schema_context: str | None,
        semantic_candidates: list[dict] | None,
    ) -> str:
        parts = [f"## User Question\n{question}\n"]

        if semantic_candidates:
            parts.append("## Potentially Relevant Governed Metrics")
            for c in semantic_candidates[:3]:
                parts.append(f"- {c['name']}: {c['description']}")
            parts.append("")

        if schema_context:
            parts.append(schema_context)
            parts.append("")

        if reference_content:
            parts.append("## Reference Documentation\n")
            parts.append(reference_content)
            parts.append("")

        parts.append(
            "## Instructions\n"
            "Generate a DuckDB SQL query to answer the user's question. "
            "Follow the analyst playbook: clarify any ambiguity in your "
            "reasoning, use the reference doc guidance for table selection "
            "and gotchas, and review your SQL for common errors before "
            "outputting it.\n\n"
            "Output format:\n"
            "REASONING: <your step-by-step reasoning>\n"
            "WARNINGS: <any caveats about the query, comma-separated, or NONE>\n"
            "TABLES: <comma-separated list of tables used>\n"
            "SQL:\n```sql\n<your query>\n```"
        )

        return "\n".join(parts)

    def _parse_response(self, text: str, reference_used: str | None) -> GeneratedSQL:
        sql = ""
        reasoning = ""
        tables: list[str] = []
        warnings: list[str] = []
        declined = False

        sql_block = _extract_between(text, "```sql", "```")
        if sql_block:
            sql = sql_block.strip()
            if sql.upper() in ("NONE", "N/A"):
                sql = ""
                declined = True
        else:
            for line in text.split("\n"):
                stripped = line.strip()
                if stripped.upper().startswith(("SELECT", "WITH")):
                    sql = text[text.index(stripped):]
                    break

        reasoning_block = _extract_after_label(text, "REASONING:")
        if reasoning_block:
            reasoning = reasoning_block.strip()

        tables_block = _extract_after_label(text, "TABLES:")
        if tables_block:
            tables = [t.strip() for t in tables_block.split(",") if t.strip()]

        warnings_block = _extract_after_label(text, "WARNINGS:")
        if warnings_block and warnings_block.strip().upper() != "NONE":
            warnings = [w.strip() for w in warnings_block.split(",") if w.strip()]

        if not tables and sql:
            for table in ["fact_daily_prices", "dim_securities", "dim_calendar"]:
                if table in sql:
                    tables.append(table)

        if not sql and not declined and re.search(r"SQL:\s*`?NONE`?", text, re.IGNORECASE):
            # Model declined without fencing NONE in a sql block
            declined = True

        return GeneratedSQL(
            sql=sql,
            reasoning=reasoning,
            reference_used=reference_used,
            tables_used=tables,
            warnings=warnings,
            declined=declined,
        )


def _extract_between(text: str, start_marker: str, end_marker: str) -> str | None:
    lower = text.lower()
    start = lower.find(start_marker.lower())
    if start == -1:
        return None
    start += len(start_marker)
    end = lower.find(end_marker.lower(), start)
    if end == -1:
        return text[start:]
    return text[start:end]


def _extract_after_label(text: str, label: str) -> str | None:
    upper = text.upper()
    idx = upper.find(label.upper())
    if idx == -1:
        return None
    after = text[idx + len(label):]
    newline = after.find("\n")
    if newline == -1:
        return after
    next_label = None
    for check_label in ["REASONING:", "WARNINGS:", "TABLES:", "SQL:"]:
        check_idx = after.upper().find(check_label)
        if check_idx > 0 and (next_label is None or check_idx < next_label):
            next_label = check_idx
    if next_label:
        return after[:next_label]
    return after[:newline] if newline != -1 else after
