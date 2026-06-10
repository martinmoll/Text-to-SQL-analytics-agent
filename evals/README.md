# Evals

Offline evaluation suite for measuring agent accuracy. Runs as part of CI on every PR that touches skills, semantic layer, or data models.

- `fixtures/` — JSON question/answer pairs per domain
- `run_evals.py` — Eval runner (scores query correctness against ground truth)
- `ablation.py` — A/B testing skill changes against the eval set
- `results/` — Stored eval results for tracking accuracy over time
