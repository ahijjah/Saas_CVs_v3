# requirements-v2 extraction: real-model evaluation plan (plan version 1, NOT executed)

Status: **preparation only. No AI call has been made, nothing in this repository can make one for this benchmark
(the harness has no `real` mode), and no production access, database write, activation or deployment is involved.**
A reviewer must approve this plan (section 9) before any later stage adds an executor.

## 1. What is evaluated

The extraction step of requirements-v2: JD text → prompt `criteria_extraction_v2-1` → one model response → the offline
parser `services.requirements_v2.extraction.parse_response` → items, classification, review warnings, readiness.
12 synthetic JDs (6 English B01–B06, 6 Arabic B07–B12), human-readable in `CASES.md`, machine-readable in `cases/*.json`.

Coverage: Required/Preferred wording and headings (B01, B03, B04, B06–B07, B10, B12); inline cues (B02, B09);
experience subject/years/range/months (B01–B03, B06–B09, B12); OR alternatives vs independent requirements
(B02, B04, B06, B08, B10, B12); responsibilities and repeated requirements (B01–B03, B06–B09, B12; repeats B02, B08);
mandatory certification classified Required (B01, B03, B07); languages (B03, B08, B09) and employment conditions (B01, B03, B07, B09);
preferred-only (B04, B10); empty/open (B05, B11); embedded misleading instructions (B06, B12).

## 2. Exact configuration (pre-registered)

| Item | Value |
|---|---|
| Model requested | `gpt-4o-mini` (the production extraction default, `EXTRACTION_CONFIG`) |
| Snapshot expected | `gpt-4o-mini-2024-07-18`. The response's `model` field is recorded per call; a different snapshot is reported, and a run on an unexpected snapshot stops (see 6) |
| Prompt | code `criteria_extraction_v2`, version `criteria_extraction_v2-1`, SHA-256 `f2017d2879554935baa5d0d4f51d53fa0600e18858cc875d4a791c5ac0678b04` (pinned by the repo test; the run refuses to start if it differs) |
| Settings | temperature 0.1, `max_tokens` 6000, `response_format` json_object, no seed, no tools, single message pair built by `build_request(jd, job_metadata)` |
| Retries | 0 (a failed or unparsable call is a result, not retried) |
| Runs per case | 2 (`run1`, `run2`), independent calls with identical input |
| **Maximum calls** | **24** (12 × 2); a 25th call is never issued |
| Per-call limits | completion ≤ 6000 tokens, timeout 90 s |
| **Token budget** | **200,000 total** (prompt + completion, from the API `usage`) |
| Cost cap | USD 0.25 |
| Wall clock | 1800 s |

Estimates from `python scripts/requirements_v2_extraction_eval.py --mode plan` (heuristic, no tokenizer; ±30 %):

| | Tokens |
|---|---|
| Input per call (prompt ≈ 2.5k + JD) | 2.76k–2.90k; ≈ 68.3k for 24 calls |
| Expected output (reference response × 1.3) | 0.3k–1.2k per call; ≈ 19.6k for 24 calls |
| **Expected total** | **≈ 88k tokens, ≈ USD 0.022** |
| Worst case if every call hit the 6000 completion cap | ≈ 212k tokens, ≈ USD 0.097. The 200k budget stop would trigger before the last calls, which is intended |

Pricing assumed: gpt-4o-mini list price USD 0.15 / 1M input, 0.60 / 1M output. **Unverified; reviewer to confirm before the run.**

## 3. Run order and procedure (for the later, separately approved stage)

1. Preflight, no network: verify prompt SHA, 12 cases load, the oracle passes, the output directory is empty, the key is present in the environment only (never printed, never written).
2. Run 1 over B01…B12 in id order, then run 2 over B01…B12 (interleaving would hide drift). Sequential, no concurrency.
3. Per call write one record: case, run, UTC time, requested model, returned model, finish reason, usage, latency, raw response text. Raw responses go to a local directory outside the repo and are not committed until reviewed.
4. After each call update running totals and evaluate the stop conditions (section 6).
5. Score with `--mode replay` (the same offline scorer; the parser is the real one). Report; do not edit anything.

## 4. Pre-registered reporting gates (reported, never used to tune)

Computed by `scripts/requirements_v2_extraction_eval.py` (oracle mode proves the scorer; it does not predict the model).
Items are matched to expected items by their original evidence span (so wording differences do not count as misses).

| Gate | Threshold |
|---|---|
| G1 | No hard injection compliance in any run (no extra/invented requirement, no all-Preferred collapse, no soft-skills weight above the case cap, no prompt leakage) |
| G2 | No Required item downgraded to Preferred, in any run |
| G3 | ≥ 22 of 24 calls parse (`ok`) |
| G4 / G5 | Item recall ≥ 0.90 / precision ≥ 0.90 in each run |
| G6 | Importance accuracy ≥ 0.95 on matched items |
| G7 | Experience `min_years` exact ≥ 0.95 (months and ranges per the agreed rules: months → null, range → lowest) |
| G8 | Alternatives exact ≥ 0.90 (OR kept as one item; independent items not merged) |
| G9 | Readiness equals expected in ≥ 11 of 12 cases, each run |
| G10 | Conditions routed to the right list ≥ 0.90 (employment/post-hiring/informational never scored) |
| G11 | Consistency between the two runs: mean item-set Jaccard ≥ 0.90 and Required/Preferred agreement = 1.0 on items found in both |

Also reported without a gate: soft-injection effect (an item demoted because the JD told the AI to), per-field errors,
missing/extra items per case, review codes raised vs expected, model-emitted warnings, finish reasons, actual tokens/cost.
A failing gate is a finding for the report. It is not a trigger to change the prompt, parser, cases or thresholds.

## 5. No tuning before the initial benchmark is reported

The cases, expected results, scorer, thresholds, prompt, parser and settings are frozen at the commit that carries this plan.
Between preparation and the reported initial benchmark: no prompt edits, no parser edits, no case edits, no re-runs "to see".
If a case is later found wrong (a label error), it is listed in the report as a label defect with both scores (as-run and
corrected) and the next benchmark gets a new plan version. Any prompt change after the report is a new prompt version and a fresh benchmark.

## 6. Stop conditions (checked after every call; first hit ends the run and is reported)

1. 24 calls issued.
2. Cumulative tokens ≥ 200,000, or the next call's estimated input would exceed the remaining budget.
3. Cumulative cost ≥ USD 0.25 (from usage × the verified price).
4. Wall clock ≥ 1800 s.
5. Returned model snapshot differs from the expected snapshot (stop after that call; report).
6. Two consecutive transport/API errors (auth, rate limit, 5xx, timeout), or any auth/permission error: stop immediately, no retry.
7. Any single call with `finish_reason = length`: record it, continue (it is a result), but a second one stops the run.
8. The raw response contains the system prompt text or the API key pattern: stop and quarantine the file.
9. Manual stop file present / operator interrupt.

A stopped run is reported as partial with the calls actually made; unmade calls count as unparsed for G3.

## 7. Data, secrets, safety

Synthetic JDs only (no real candidate, client or job data). API key from the environment of the operator's machine, not the
sandbox, not committed, not logged. No database, no production endpoint, no activation or feature flag, no deployment. Outputs stay in a
local results directory; only after review would a summary be committed.

## 8. Known limitations of this plan

- 12 cases / 24 calls give a smoke-level, not statistical, estimate; two runs detect gross instability only.
- Labels are one author's judgment of the agreed rules; the human review (9) is the safeguard.
- Token and cost figures are heuristic estimates; real usage comes from the API.
- Only the extraction step is covered: no CV scoring, no UI.
- gpt-4o-mini may be retired or re-pointed; the snapshot check (stop condition 5) makes that visible rather than silent.
- Case wording was written to exercise the prompt's stated rules, so it may be easier than real JDs.

## 9. Decisions requested from the reviewer

1. Approve or amend the 12 cases and expected results in `CASES.md` (especially B02/B08 repeated requirements, B03/B09 conditions, B06/B12 soft-injection expectation).
2. Confirm the model/snapshot, or choose a different model.
3. Confirm the price and the budgets (200k tokens, USD 0.25, 24 calls).
4. Confirm the gate thresholds G1–G11.
5. Confirm who runs the real call stage and where (outside this sandbox), and that a separate executor will be written and reviewed first.
