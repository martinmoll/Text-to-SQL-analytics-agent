# Eval Timeline

Running log of eval suite results over time, so accuracy changes can be tracked
against code/skill/semantic-layer changes. Append a new row (and a notes entry
below) after every meaningful eval run.

**How to reproduce a run:**

```bash
# Resolution-only (free, no API key — tests the deterministic semantic layer)
python -m evals.run_evals --no-llm --verbose --save

# Full pipeline (needs ANTHROPIC_API_KEY and data/warehouse.duckdb)
python -m evals.run_evals --verbose --save

# Compare two runs for regressions
python -m evals.ablation evals/results/<baseline>.json evals/results/<candidate>.json
```

Pass criterion: weighted score ≥ 0.5 per case (metric resolution 0.3, SQL
pattern checks, execution, numeric accuracy within per-case tolerance).

## Results Log

| Date | Commit | Mode | Passed | Pass rate | Avg score | fund | mkt | port | ret | risk | Results file |
|------|--------|------|--------|-----------|-----------|------|-----|------|-----|------|--------------|
| 2026-07-02 | `29de1cd` | resolution-only | 37/52 | 71% | 0.635 | 5/5 | 4/12 | 4/8 | 14/15 | 10/12 | `eval_20260702_152416.json` |
| 2026-07-02 | `29de1cd` | **full pipeline** | **47/52** | **90%** | **0.853** | 4/5 | 9/12 | 8/8 | 14/15 | 12/12 | `eval_20260702_155945.json` |
| 2026-07-02 | working tree (fix 1) | full pipeline | 47/52 | 90% | 0.871 | 4/5 | 9/12 | 8/8 | 14/15 | 12/12 | `eval_20260702_171635.json` |
| 2026-07-02 | working tree (fix 2) | resolution-only | 39/52 | 75% | — | 5/5 | 5/12 | 5/8 | 14/15 | 10/12 | (not saved) |
| 2026-07-02 | working tree (fix 2) | full pipeline | 48/52 | 92% | 0.909 | 2/5 | 11/12 | 8/8 | 15/15 | 12/12 | `eval_20260702_174150.json` |
| 2026-07-02 | working tree (fix 3) | **full pipeline** | **50/52** | **96%** | **0.929** | 5/5 | 10/12 | 8/8 | 15/15 | 12/12 | `eval_20260702_181034.json` |

Domain key: fund = fundamentals (5 cases), mkt = market overview (12), port = portfolio (8), ret = returns & risk (15), risk = risk/beta (12).

## Run Notes

### 2026-07-02 — resolution-only baseline (`29de1cd`)

First saved eval run for the repo (`evals/results/` was empty until now).

- **37/52 passed (71%), avg score 0.635.** This mode disables the LLM, so it
  measures only the deterministic semantic resolver + compiler.
- Market (4/12) and portfolio (4/8) score low **by design** in this mode —
  those domains are built to fall through to LLM SQL generation, which is off.
- Counting only the domains the semantic layer is designed to cover
  (fundamentals + returns + risk): **29/32 = 91%**.
- Recurring gaps in failing/low-scoring cases: compiled SQL lacks
  `security_type = 'equity'` filters and `sector` joins for segment-style
  questions (risk_02, risk_05, risk_12), no rolling-window support in the
  compiler (risk_11), and parameter extraction misses a custom risk-free rate
  ("4%" → still compiles with the 0.05 default, risk_09). These are concrete
  semantic-layer/resolver improvements to target.

### 2026-07-02 — full pipeline baseline (`29de1cd`)

First full-agent run (clarify → resolve → LLM generation → adversarial review
→ execution against `data/warehouse.duckdb` built 2026-06-16). Model:
claude-sonnet-4-6.

- **47/52 passed (90%), avg score 0.853.** Headline accuracy figure for the
  system as shipped.
- Adding the LLM fallback lifted exactly the domains the semantic layer
  doesn't cover: portfolio 4/8 → **8/8**, market 4/12 → **9/12**,
  risk 10/12 → **12/12**. The LLM also fixed the resolution-only gaps
  (rolling 60-day beta, sector filters, custom 4% risk-free rate all pass).
- The 5 failures:
  - `fund_05` — the one regression vs. resolution-only: with the LLM enabled,
    the agent attempted an answer instead of explaining that fundamentals data
    is not available (the honest-failure behavior the fixture requires).
  - `mkt_01`, `mkt_02`, `mkt_11` — generated SQL didn't use the expected
    `adjusted_close`-based patterns (e.g. used close or a different formula);
    mkt_02 also missed the `security_type = 'index'` filter.
  - `ret_15` — "annualized return over 3 years" resolved to `sharpe_ratio`
    instead of an annualized-return calculation (resolver phrase-map gap:
    no `annualized return` metric/alias exists).
- Next targets, in expected-impact order: add an `annualized_return` metric or
  alias (ret_15), strengthen the fundamentals not-available instruction in the
  clarify/generation prompts (fund_05), and add index-handling guidance to
  `references/market_overview.md` (mkt cases).

### 2026-07-02 — fix round 1: annualized_return metric + honest-failure path

Changes: new governed `annualized_return` metric (14 total) with phrase-map
aliases and relative-period parsing ("last 3 years") in the resolver;
generator system prompt now instructs declining fundamentals questions with
`NONE`; orchestrator surfaces the generator's reasoning as the answer when no
SQL is produced (previously discarded — the actual fund_05 bug).

- Full run: **47/52 (90%), avg score 0.853 → 0.871.** fund_05 fixed, but
  ret_15 still failed and fund_02 flipped to failing — both traced to the
  same root cause: the CLARIFY step *paraphrases* the question, and the
  resolver ran on the paraphrase. When the exact metric phrase was reworded
  away, the fuzzy `lookup()` fallback mis-resolved via stopword-substring
  scoring (e.g. "on" matching inside "only" pushed `cumulative_return` over
  the threshold for an EV/EBITDA question).

### 2026-07-02 — fix round 2: deterministic resolution root-cause fixes

Changes: (1) orchestrator resolves the user's *original* wording first and
only falls back to the LLM-clarified paraphrase — removing LLM
nondeterminism from the governed path; (2) `compiler.lookup()` rewritten
from naive substring scoring to word-boundary token overlap with a stopword
list and a double-weight bonus for tokens matching the metric name.

- Resolution-only: **37/52 → 39/52 (75%)**, fundamentals avg score 0.88 → 1.00
  — the lookup fix eliminated false resolutions.
- Paraphrase robustness verified offline: "annualized rate of return" (the
  kind of rewrite CLARIFY produces) now resolves to `annualized_return`.
- All 146 unit tests pass.
- Full run: **48/52 (92%), avg 0.909.** ret_15 now scores 1.00; returns,
  risk, and portfolio domains all perfect. But fundamentals dropped to 2/5 —
  same code that passed fund_05 in fix 1 failed it here, exposing that the
  honest-failure behavior depended on LLM sampling luck: whether the
  refusal's *wording* happened to contain the eval's expected phrases.

### 2026-07-02 — fix round 3: deterministic honest failure

The fundamentals domain flapped across runs (5/5 → 4/5 → 2/5) with
functionally identical code. Root cause: the "data not available"
explanation was generated fresh by the LLM each time, and the eval (fairly)
expects an explicit availability statement. Changes:

1. CLARIFY prompt now knows the warehouse has no fundamentals data — it
   stops asking counter-questions ("which date period?") about data that
   does not exist (fund_05's failure path this round).
2. The generator's NONE decline is parsed into an explicit `declined` flag
   (with a fallback matcher for unfenced `SQL: NONE`).
3. The orchestrator states the availability fact deterministically —
   "This question requires data that is not available in the warehouse." —
   and appends the LLM's reasoning for specifics. Honest failure is now
   code, not sampling luck.

- Fundamentals-only spot run: **5/5**. All 146 unit tests pass.
- Final full run: **50/52 (96%), avg score 0.929** — the headline figure.
  fund 5/5, ret 15/15, risk 12/12, port 8/8, mkt 10/12.
- Remaining failures (both market overview, both index-handling):
  - `mkt_02` — generated SQL missed the `security_type = 'index'` filter.
  - `mkt_05` — exchange/adjusted_close patterns missing in a cross-exchange
    comparison.
  - Next target: index-handling and cross-exchange guidance in
    `skills/references/market_overview.md`.
