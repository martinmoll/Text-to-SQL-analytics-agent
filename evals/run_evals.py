"""
Eval runner: scores the analytics agent against fixture Q&A pairs.

Evaluates three dimensions:
1. Metric resolution — did the semantic layer resolve the correct metric?
2. SQL correctness — does the generated SQL contain expected patterns?
3. Answer accuracy — does the numeric result match ground truth? (when available)

Usage:
    python -m evals.run_evals                          # run all fixtures
    python -m evals.run_evals --fixture returns         # run one domain
    python -m evals.run_evals --tags semantic_layer     # filter by tag
    python -m evals.run_evals --no-llm                  # skip LLM calls (resolution only)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"
RESULTS_DIR = Path(__file__).parent / "results"


@dataclass
class EvalCase:
    id: str
    question: str
    expected_metric: str | None
    expected_table: str | None
    expected_filters: dict
    sql_must_contain: list[str]
    sql_must_not_contain: list[str]
    ground_truth: float | None
    tolerance: float | None
    tags: list[str] = field(default_factory=list)
    expected_behavior: str | None = None


@dataclass
class EvalResult:
    case_id: str
    question: str

    # Metric resolution
    metric_resolved: bool = False
    expected_metric: str | None = None
    actual_metric: str | None = None
    metric_match: bool | None = None

    # SQL quality
    sql_generated: bool = False
    sql: str = ""
    sql_contains_pass: list[str] = field(default_factory=list)
    sql_contains_fail: list[str] = field(default_factory=list)
    sql_not_contains_pass: list[str] = field(default_factory=list)
    sql_not_contains_fail: list[str] = field(default_factory=list)

    # Execution
    executed: bool = False
    row_count: int = 0
    execution_error: str | None = None

    # Accuracy
    ground_truth: float | None = None
    actual_value: float | None = None
    within_tolerance: bool | None = None

    # Behavior
    expected_behavior: str | None = None
    behavior_match: bool | None = None

    # Overall
    passed: bool = False
    score: float = 0.0
    notes: list[str] = field(default_factory=list)


def load_fixtures(fixture_name: str | None = None, tags: list[str] | None = None) -> list[EvalCase]:
    cases: list[EvalCase] = []

    if fixture_name:
        patterns = [f"{fixture_name}_evals.json", f"{fixture_name}.json"]
    else:
        patterns = ["*_evals.json"]

    for pattern in patterns:
        for path in FIXTURES_DIR.glob(pattern):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            for item in data:
                case = EvalCase(
                    id=item["id"],
                    question=item["question"],
                    expected_metric=item.get("expected_metric"),
                    expected_table=item.get("expected_table"),
                    expected_filters=item.get("expected_filters", {}),
                    sql_must_contain=item.get("sql_must_contain", []),
                    sql_must_not_contain=item.get("sql_must_not_contain", []),
                    ground_truth=item.get("ground_truth"),
                    tolerance=item.get("tolerance"),
                    tags=item.get("tags", []),
                    expected_behavior=item.get("expected_behavior"),
                )
                if tags:
                    if any(t in case.tags for t in tags):
                        cases.append(case)
                else:
                    cases.append(case)

    return cases


def evaluate_resolution(case: EvalCase, resolution) -> EvalResult:
    """Evaluate only the semantic resolution step (no LLM needed)."""
    result = EvalResult(
        case_id=case.id,
        question=case.question,
        expected_metric=case.expected_metric,
        ground_truth=case.ground_truth,
        expected_behavior=case.expected_behavior,
    )

    result.metric_resolved = resolution.found
    result.actual_metric = resolution.metric_name

    if case.expected_metric:
        result.metric_match = (resolution.metric_name == case.expected_metric)
    elif case.expected_metric is None and not resolution.found:
        result.metric_match = True

    if resolution.found and resolution.compiled:
        result.sql_generated = True
        result.sql = resolution.compiled.sql
        _check_sql_patterns(result, case)

    result.score = _compute_score(result)
    result.passed = result.score >= 0.5
    return result


def evaluate_full(case: EvalCase, agent) -> EvalResult:
    """Run the full agent pipeline and evaluate the response."""
    result = EvalResult(
        case_id=case.id,
        question=case.question,
        expected_metric=case.expected_metric,
        ground_truth=case.ground_truth,
        expected_behavior=case.expected_behavior,
    )

    try:
        response = agent.answer(case.question)
    except Exception as e:
        result.notes.append(f"Agent error: {e}")
        return result

    # Check metric resolution from trace
    for step in response.trace:
        if step.get("step") == "resolve":
            result.metric_resolved = step.get("found", False)
            result.actual_metric = step.get("metric")
            break

    if case.expected_metric:
        result.metric_match = (result.actual_metric == case.expected_metric)
    elif case.expected_metric is None and not result.metric_resolved:
        result.metric_match = True

    # Check SQL
    result.sql = response.sql
    result.sql_generated = bool(response.sql.strip())
    if result.sql_generated:
        _check_sql_patterns(result, case)

    # Check execution
    if response.data is not None:
        result.executed = True
        result.row_count = len(response.data)

        if case.ground_truth is not None and case.tolerance is not None:
            _check_accuracy(result, response.data, case)

    # Check expected behavior (e.g., fundamentals not available)
    if case.expected_behavior == "agent_should_explain_data_not_available":
        answer_lower = response.answer.lower()
        data_not_available_phrases = [
            "not available", "not yet", "no data", "not ingested",
            "not populated", "cannot", "don't have", "do not have",
        ]
        result.behavior_match = any(p in answer_lower for p in data_not_available_phrases)
        if not result.behavior_match:
            result.notes.append("Expected agent to explain data is not available")

    result.score = _compute_score(result)
    result.passed = result.score >= 0.5
    return result


def _check_sql_patterns(result: EvalResult, case: EvalCase) -> None:
    sql_upper = result.sql.upper()
    for pattern in case.sql_must_contain:
        if pattern.upper() in sql_upper:
            result.sql_contains_pass.append(pattern)
        else:
            result.sql_contains_fail.append(pattern)
            result.notes.append(f"SQL missing expected pattern: {pattern}")

    for pattern in case.sql_must_not_contain:
        if pattern.upper() in sql_upper:
            result.sql_not_contains_fail.append(pattern)
            result.notes.append(f"SQL contains forbidden pattern: {pattern}")
        else:
            result.sql_not_contains_pass.append(pattern)


def _check_accuracy(result: EvalResult, df, case: EvalCase) -> None:
    import pandas as pd
    numeric_cols = df.select_dtypes(include=["number"]).columns
    if len(numeric_cols) == 0:
        return

    value_col = None
    for candidate in ["value", "cumulative_return", "sharpe_ratio", "beta",
                       "correlation", "ann_volatility", "max_drawdown",
                       "annualized_volatility", "sortino_ratio"]:
        if candidate in df.columns:
            value_col = candidate
            break
    if value_col is None:
        value_col = numeric_cols[-1]

    if len(df) == 1:
        val = df[value_col].iloc[0]
        if pd.notna(val):
            result.actual_value = float(val)
            result.within_tolerance = abs(val - case.ground_truth) <= case.tolerance
    elif "ticker" in df.columns and case.expected_filters.get("ticker"):
        ticker = case.expected_filters["ticker"]
        if isinstance(ticker, str):
            row = df[df["ticker"] == ticker]
            if len(row) == 1:
                val = row[value_col].iloc[0]
                if pd.notna(val):
                    result.actual_value = float(val)
                    result.within_tolerance = abs(val - case.ground_truth) <= case.tolerance


def _compute_score(result: EvalResult) -> float:
    scores: list[float] = []
    weights: list[float] = []

    # Metric match (weight 0.3)
    if result.metric_match is not None:
        scores.append(1.0 if result.metric_match else 0.0)
        weights.append(0.3)

    # SQL patterns (weight 0.4)
    total_patterns = len(result.sql_contains_pass) + len(result.sql_contains_fail)
    total_negatives = len(result.sql_not_contains_pass) + len(result.sql_not_contains_fail)
    all_patterns = total_patterns + total_negatives
    if all_patterns > 0:
        passed = len(result.sql_contains_pass) + len(result.sql_not_contains_pass)
        scores.append(passed / all_patterns)
        weights.append(0.4)

    # Execution (weight 0.1)
    if result.sql_generated:
        scores.append(1.0 if result.executed else 0.0)
        weights.append(0.1)

    # Accuracy (weight 0.2)
    if result.within_tolerance is not None:
        scores.append(1.0 if result.within_tolerance else 0.0)
        weights.append(0.2)

    # Behavior match
    if result.behavior_match is not None:
        scores.append(1.0 if result.behavior_match else 0.0)
        weights.append(0.5)

    if not weights:
        return 0.0
    return sum(s * w for s, w in zip(scores, weights)) / sum(weights)


def print_results(results: list[EvalResult], verbose: bool = False) -> None:
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    pct = 100 * passed / total if total > 0 else 0

    print(f"\n{'=' * 60}")
    print(f"EVAL RESULTS: {passed}/{total} passed ({pct:.1f}%)")
    print(f"{'=' * 60}\n")

    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"  [{status}] {r.case_id}: {r.question[:60]}...")
        print(f"         score={r.score:.2f} metric={r.actual_metric or 'none'}")
        if verbose:
            if r.sql_contains_fail:
                print(f"         missing: {r.sql_contains_fail}")
            if r.sql_not_contains_fail:
                print(f"         forbidden: {r.sql_not_contains_fail}")
            if r.notes:
                for note in r.notes:
                    print(f"         note: {note}")
        print()

    # Summary by domain
    domains: dict[str, list[EvalResult]] = {}
    for r in results:
        domain = r.case_id.split("_")[0]
        domains.setdefault(domain, []).append(r)

    print("Summary by domain:")
    for domain, domain_results in sorted(domains.items()):
        dp = sum(1 for r in domain_results if r.passed)
        dt = len(domain_results)
        avg = sum(r.score for r in domain_results) / dt if dt > 0 else 0
        print(f"  {domain:10s}: {dp}/{dt} passed, avg score {avg:.2f}")

    print()


def save_results(results: list[EvalResult], output_path: Path | None = None) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if output_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = RESULTS_DIR / f"eval_{timestamp}.json"

    data = []
    for r in results:
        data.append({
            "case_id": r.case_id,
            "question": r.question,
            "passed": r.passed,
            "score": r.score,
            "metric_match": r.metric_match,
            "actual_metric": r.actual_metric,
            "expected_metric": r.expected_metric,
            "sql_generated": r.sql_generated,
            "sql_contains_pass": r.sql_contains_pass,
            "sql_contains_fail": r.sql_contains_fail,
            "executed": r.executed,
            "row_count": r.row_count,
            "ground_truth": r.ground_truth,
            "actual_value": r.actual_value,
            "within_tolerance": r.within_tolerance,
            "notes": r.notes,
        })

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "total": len(results),
            "passed": sum(1 for r in results if r.passed),
            "avg_score": sum(r.score for r in results) / len(results) if results else 0,
            "results": data,
        }, f, indent=2)

    return output_path


def main():
    parser = argparse.ArgumentParser(description="Run analytics agent evaluations")
    parser.add_argument("--fixture", type=str, help="Run specific fixture (returns, risk, portfolio, market, fundamentals)")
    parser.add_argument("--tags", type=str, nargs="+", help="Filter by tags")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM calls — evaluate resolution only")
    parser.add_argument("--db", type=str, help="Path to DuckDB warehouse")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed results")
    parser.add_argument("--save", action="store_true", help="Save results to JSON")
    args = parser.parse_args()

    cases = load_fixtures(fixture_name=args.fixture, tags=args.tags)
    if not cases:
        print("No eval cases found.")
        sys.exit(1)

    print(f"Loaded {len(cases)} eval cases")

    if args.no_llm:
        from agent.semantic_resolver import SemanticResolver
        resolver = SemanticResolver()
        results = []
        for case in cases:
            resolution = resolver.resolve(case.question)
            result = evaluate_resolution(case, resolution)
            results.append(result)
    else:
        from agent.orchestrator import AnalyticsAgent
        db_path = args.db or "data/warehouse.duckdb"
        agent = AnalyticsAgent(db_path=db_path)
        results = []
        for case in cases:
            print(f"  Evaluating: {case.id}...", end=" ", flush=True)
            result = evaluate_full(case, agent)
            print("PASS" if result.passed else "FAIL")
            results.append(result)

    print_results(results, verbose=args.verbose)

    if args.save:
        path = save_results(results)
        print(f"Results saved to {path}")

    passed = sum(1 for r in results if r.passed)
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
