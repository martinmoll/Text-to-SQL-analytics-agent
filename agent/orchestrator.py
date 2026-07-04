"""
Main agent orchestrator: receives a natural-language question and follows
the analyst skill's 6-step workflow to produce an answer.

Pipeline: CLARIFY → RESOLVE → FIND → QUERY → REVIEW → EXECUTE → DELIVER
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pandas as pd
import anthropic

from agent.semantic_resolver import SemanticResolver, Resolution
from agent.sql_generator import SQLGenerator, GeneratedSQL
from agent.reviewer import SQLReviewer, ReviewResult
from agent.response_formatter import ResponseFormatter, AgentResponse


DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "warehouse.duckdb"

_CLARIFY_SYSTEM_PROMPT = """\
You are a financial analytics assistant. Given a user's question about \
financial data, determine the precise intent. Output a JSON object with:

{
  "interpreted_question": "<the question as you understand it>",
  "metric_type": "<return|volatility|sharpe|sortino|drawdown|beta|correlation|volume|price|ranking|comparison|other>",
  "tickers": [<list of ticker symbols mentioned, or empty>],
  "time_period": "<specific period like 'Q1 2024', '2024', 'last year', or null>",
  "needs_clarification": false,
  "clarification_question": null,
  "ambiguities_resolved": {
    "return_type": "<simple|log|cumulative|null>",
    "benchmark": "<ticker or null>",
    "scope": "<all_equities|us_only|oslo_only|specific_tickers|null>"
  }
}

If the question is clear enough to proceed, set needs_clarification to false \
and fill in your best interpretation. Only ask for clarification if genuinely \
ambiguous AND it would change the SQL significantly.

The warehouse contains ONLY daily price/volume data and security metadata. \
There is NO fundamentals data (no earnings, revenue, P/E, price-to-book, \
EPS, dividends, balance sheet items). If the question requires fundamentals \
data, do NOT ask for clarification — set needs_clarification to false and \
pass the question through unchanged so the pipeline can explain the data is \
not available.

Available tickers: AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA, JPM, V, JNJ, \
PG, UNH, HD, MA, PFE (US); EQNR.OL, DNB.OL, MOWI.OL, ORK.OL, TEL.OL, \
AKRBP.OL (Oslo Bors); ^GSPC, ^OEX, ^IXIC (indices).
"""


@dataclass
class TraceStep:
    step: str
    detail: str
    data: dict = field(default_factory=dict)


class AnalyticsAgent:
    def __init__(
        self,
        db_path: str | Path | None = None,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-6",
        review_enabled: bool = True,
    ) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.model = model
        self.review_enabled = review_enabled

        self.resolver = SemanticResolver()
        self.generator = SQLGenerator(api_key=self.api_key, model=self.model)
        self.reviewer = SQLReviewer(api_key=self.api_key, model=self.model)
        self.formatter = ResponseFormatter()
        self.client = anthropic.Anthropic(api_key=self.api_key)

    def answer(self, question: str) -> AgentResponse:
        """Process a natural-language question through the full pipeline."""
        trace: list[dict] = []

        # Step 1: CLARIFY
        clarification = self._clarify(question)
        trace.append({
            "step": "clarify",
            "interpreted": clarification.get("interpreted_question", question),
            "metric_type": clarification.get("metric_type"),
            "error": clarification.get("clarify_error"),
        })

        if clarification.get("needs_clarification"):
            return AgentResponse(
                question=question,
                answer=clarification.get("clarification_question", "Could you clarify your question?"),
                methodology="Clarification needed before proceeding.",
                sql="",
                trace=trace,
            )

        effective_question = clarification.get("interpreted_question", question)

        # Step 2: RESOLVE — check semantic layer. Resolve against the user's
        # original wording first: phrase matching is deterministic on it,
        # while the LLM clarification may paraphrase away the exact metric
        # phrase. Fall back to the clarified text only if the original misses.
        resolution = self.resolver.resolve(question)
        if not resolution.found and effective_question != question:
            resolution = self.resolver.resolve(effective_question)
        trace.append({
            "step": "resolve",
            "found": resolution.found,
            "metric": resolution.metric_name,
            "candidates": [c["name"] for c in resolution.candidates],
        })

        sql: str
        metric_source: str
        tables_used: list[str]
        warnings: list[str] = []
        reasoning: str | None = None
        declined: bool = False
        metric_name: str | None = None
        metric_description: str | None = None
        metric_notes: str | None = None

        if resolution.found and resolution.compiled:
            # Semantic layer hit — use compiled SQL
            sql = resolution.compiled.sql
            metric_source = "semantic_layer"
            metric_name = resolution.compiled.metric_name
            metric_description = resolution.compiled.description
            metric_notes = resolution.compiled.notes
            tables_used = self._extract_tables(sql)
            trace.append({
                "step": "query",
                "source": "semantic_layer",
                "metric": metric_name,
            })
        else:
            # Steps 3-4: FIND + QUERY — route to reference doc, and generate SQL
            generated = self.generator.generate(
                question=effective_question,
                route_hint=resolution.route_hint,
                schema_context=self.resolver.get_schema_context(),
                semantic_candidates=resolution.candidates,
            )
            sql = generated.sql
            metric_source = "llm_generated"
            tables_used = generated.tables_used or self._extract_tables(sql)
            warnings = generated.warnings
            reasoning = generated.reasoning
            declined = generated.declined
            trace.append({
                "step": "query",
                "source": "llm_generated",
                "reference": generated.reference_used,
                "reasoning_preview": (reasoning or "")[:200],
            })

        if not sql.strip():
            # The generator declining (NONE) has exactly one meaning per its
            # prompt: the required data is not in the warehouse. State that
            # deterministically; the LLM reasoning adds the specifics.
            if declined:
                answer = "This question requires data that is not available in the warehouse."
            else:
                answer = (
                    "Unable to generate a SQL query for this question — it "
                    "likely requires data that is not available in the warehouse."
                )
            if reasoning and reasoning.strip():
                answer = f"{answer}\n\n{reasoning.strip()}"
            return AgentResponse(
                question=question,
                answer=answer,
                methodology="The semantic layer had no coverage and no SQL was generated.",
                sql="",
                warnings=["No SQL was generated"],
                trace=trace,
            )

        # Step 5: REVIEW
        review_summary: str | None = None
        if self.review_enabled:
            review = self.reviewer.review(sql, effective_question, metric_source)
            review_summary = review.summary
            trace.append({
                "step": "review",
                "passed": review.passed,
                "issues": len(review.issues),
                "summary": review.summary,
            })

            if review.revised_sql and not review.passed:
                sql = review.revised_sql
                warnings.append(f"SQL was revised by reviewer: {review.summary}")

            for issue in review.issues:
                if issue.severity == "warning":
                    warnings.append(f"[{issue.category}] {issue.message}")

        # Step 6: EXECUTE
        result_df = self._execute(sql)
        trace.append({
            "step": "execute",
            "success": result_df is not None,
            "rows": len(result_df) if result_df is not None else 0,
        })

        if result_df is None:
            return AgentResponse(
                question=question,
                answer="Query execution failed. The SQL may contain errors.",
                methodology="Query was generated but failed during execution.",
                sql=sql,
                warnings=warnings + ["Execution failed — check SQL syntax and data availability"],
                trace=trace,
            )

        # Step 7: DELIVER
        return self.formatter.format(
            question=question,
            sql=sql,
            result_df=result_df,
            metric_source=metric_source,
            tables_used=tables_used,
            review_summary=review_summary,
            warnings=warnings,
            reasoning=reasoning,
            metric_name=metric_name,
            metric_description=metric_description,
            metric_notes=metric_notes,
            trace=trace,
        )

    def _clarify(self, question: str) -> dict:
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=512,
                system=_CLARIFY_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": question}],
            )
            import json
            text = response.content[0].text
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(text[start:end])
        except Exception as e:
            return {
                "interpreted_question": question,
                "needs_clarification": False,
                "clarify_error": str(e),
            }
        return {"interpreted_question": question, "needs_clarification": False}

    def _execute(self, sql: str) -> pd.DataFrame | None:
        try:
            conn = duckdb.connect(str(self.db_path), read_only=True)
            result = conn.execute(sql).fetchdf()
            conn.close()
            return result
        except Exception:
            return None

    def _extract_tables(self, sql: str) -> list[str]:
        tables = []
        for table in ["fact_daily_prices", "dim_securities", "dim_calendar"]:
            if table in sql.lower():
                tables.append(table)
        return tables


def main():
    """CLI entry point for interactive use."""
    import sys

    db_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB_PATH
    agent = AnalyticsAgent(db_path=db_path)

    print("Financial Analytics Agent")
    print("=" * 50)
    print("Ask questions about stocks, returns, risk, and more.")
    print("Type 'quit' to exit.\n")

    while True:
        try:
            question = input("Q: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if question.lower() in ("quit", "exit", "q"):
            print("Goodbye!")
            break

        if not question:
            continue

        print("\nProcessing...\n")
        response = agent.answer(question)
        print(response.to_markdown())
        print()


if __name__ == "__main__":
    main()
