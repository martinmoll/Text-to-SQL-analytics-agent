"""
Ablation testing: compares eval results before and after changes to skills,
semantic layer, or reference docs.

Loads two eval result JSON files (baseline vs candidate) and reports
per-case and aggregate differences.

Usage:
    python -m evals.ablation baseline.json candidate.json
    python -m evals.ablation --baseline-dir results/ --candidate-dir results/
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path


RESULTS_DIR = Path(__file__).parent / "results"


@dataclass
class CaseComparison:
    case_id: str
    question: str
    baseline_score: float
    candidate_score: float
    baseline_passed: bool
    candidate_passed: bool
    delta: float
    status: str  # "improved", "regressed", "unchanged"


@dataclass
class AblationReport:
    baseline_file: str
    candidate_file: str
    total_cases: int
    baseline_pass_rate: float
    candidate_pass_rate: float
    baseline_avg_score: float
    candidate_avg_score: float
    improved: int
    regressed: int
    unchanged: int
    comparisons: list[CaseComparison]
    new_failures: list[CaseComparison]
    new_passes: list[CaseComparison]


def load_eval_results(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def compare(baseline_path: Path, candidate_path: Path) -> AblationReport:
    baseline = load_eval_results(baseline_path)
    candidate = load_eval_results(candidate_path)

    baseline_by_id = {r["case_id"]: r for r in baseline["results"]}
    candidate_by_id = {r["case_id"]: r for r in candidate["results"]}

    common_ids = set(baseline_by_id.keys()) & set(candidate_by_id.keys())
    if not common_ids:
        print("Warning: no common case IDs between baseline and candidate")

    comparisons: list[CaseComparison] = []
    for cid in sorted(common_ids):
        b = baseline_by_id[cid]
        c = candidate_by_id[cid]
        delta = c["score"] - b["score"]
        if delta > 0.01:
            status = "improved"
        elif delta < -0.01:
            status = "regressed"
        else:
            status = "unchanged"

        comparisons.append(CaseComparison(
            case_id=cid,
            question=b.get("question", ""),
            baseline_score=b["score"],
            candidate_score=c["score"],
            baseline_passed=b["passed"],
            candidate_passed=c["passed"],
            delta=delta,
            status=status,
        ))

    improved = sum(1 for c in comparisons if c.status == "improved")
    regressed = sum(1 for c in comparisons if c.status == "regressed")
    unchanged = sum(1 for c in comparisons if c.status == "unchanged")

    new_failures = [c for c in comparisons if c.baseline_passed and not c.candidate_passed]
    new_passes = [c for c in comparisons if not c.baseline_passed and c.candidate_passed]

    total = len(comparisons)
    return AblationReport(
        baseline_file=str(baseline_path),
        candidate_file=str(candidate_path),
        total_cases=total,
        baseline_pass_rate=baseline["passed"] / baseline["total"] if baseline["total"] else 0,
        candidate_pass_rate=candidate["passed"] / candidate["total"] if candidate["total"] else 0,
        baseline_avg_score=baseline.get("avg_score", 0),
        candidate_avg_score=candidate.get("avg_score", 0),
        improved=improved,
        regressed=regressed,
        unchanged=unchanged,
        comparisons=comparisons,
        new_failures=new_failures,
        new_passes=new_passes,
    )


def print_report(report: AblationReport) -> None:
    print(f"\n{'=' * 60}")
    print("ABLATION REPORT")
    print(f"{'=' * 60}")
    print(f"Baseline:  {report.baseline_file}")
    print(f"Candidate: {report.candidate_file}")
    print(f"Cases compared: {report.total_cases}")
    print()

    print(f"  Pass rate: {report.baseline_pass_rate:.1%} → {report.candidate_pass_rate:.1%} "
          f"({'+'if report.candidate_pass_rate >= report.baseline_pass_rate else ''}"
          f"{(report.candidate_pass_rate - report.baseline_pass_rate):.1%})")
    print(f"  Avg score: {report.baseline_avg_score:.3f} → {report.candidate_avg_score:.3f} "
          f"({'+'if report.candidate_avg_score >= report.baseline_avg_score else ''}"
          f"{(report.candidate_avg_score - report.baseline_avg_score):.3f})")
    print()
    print(f"  Improved:  {report.improved}")
    print(f"  Regressed: {report.regressed}")
    print(f"  Unchanged: {report.unchanged}")
    print()

    if report.new_failures:
        print("NEW FAILURES (were passing, now failing):")
        for c in report.new_failures:
            print(f"  [{c.case_id}] {c.question[:50]}... ({c.baseline_score:.2f} → {c.candidate_score:.2f})")
        print()

    if report.new_passes:
        print("NEW PASSES (were failing, now passing):")
        for c in report.new_passes:
            print(f"  [{c.case_id}] {c.question[:50]}... ({c.baseline_score:.2f} → {c.candidate_score:.2f})")
        print()

    if report.regressed:
        print("ALL REGRESSIONS:")
        for c in sorted(report.comparisons, key=lambda x: x.delta):
            if c.status == "regressed":
                print(f"  [{c.case_id}] delta={c.delta:+.2f} ({c.baseline_score:.2f} → {c.candidate_score:.2f})")
        print()


def find_latest_results(directory: Path) -> Path | None:
    results = sorted(directory.glob("eval_*.json"))
    return results[-1] if results else None


def main():
    parser = argparse.ArgumentParser(description="Compare eval results (ablation testing)")
    parser.add_argument("baseline", nargs="?", type=str, help="Baseline results JSON")
    parser.add_argument("candidate", nargs="?", type=str, help="Candidate results JSON")
    parser.add_argument("--baseline-dir", type=str, help="Directory with baseline results (uses latest)")
    parser.add_argument("--candidate-dir", type=str, help="Directory with candidate results (uses latest)")
    parser.add_argument("--fail-on-regression", action="store_true", help="Exit with code 1 if any regressions")
    args = parser.parse_args()

    if args.baseline:
        baseline_path = Path(args.baseline)
    elif args.baseline_dir:
        baseline_path = find_latest_results(Path(args.baseline_dir))
        if not baseline_path:
            print(f"No results found in {args.baseline_dir}")
            sys.exit(1)
    else:
        results = sorted(RESULTS_DIR.glob("eval_*.json"))
        if len(results) < 2:
            print("Need at least 2 result files in results/ for comparison, or specify paths.")
            sys.exit(1)
        baseline_path = results[-2]

    if args.candidate:
        candidate_path = Path(args.candidate)
    elif args.candidate_dir:
        candidate_path = find_latest_results(Path(args.candidate_dir))
        if not candidate_path:
            print(f"No results found in {args.candidate_dir}")
            sys.exit(1)
    else:
        results = sorted(RESULTS_DIR.glob("eval_*.json"))
        candidate_path = results[-1]

    report = compare(baseline_path, candidate_path)
    print_report(report)

    if args.fail_on_regression and report.new_failures:
        print(f"FAILED: {len(report.new_failures)} new failure(s)")
        sys.exit(1)


if __name__ == "__main__":
    main()
