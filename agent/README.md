# Agent

The LLM-powered analytics agent that orchestrates the text-to-SQL pipeline.

- `orchestrator.py` — Main agent loop (receive question → skills → semantic layer → SQL → review → respond)
- `semantic_resolver.py` — Checks semantic layer for governed metric definitions first
- `sql_generator.py` — Skill-guided text-to-SQL generation
- `reviewer.py` — Adversarial review sub-agent (checks for wrong joins, missing filters, grain mismatches)
- `response_formatter.py` — Provenance footer with source tier, freshness, and confidence
