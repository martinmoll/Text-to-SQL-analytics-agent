# GitHub Workflows

CI/CD pipelines.

- `eval_ci.yml` — Runs the eval suite on every PR that touches `skills/`, `semantic_layer/`, or `data/models/`. PR cannot merge if accuracy drops.
