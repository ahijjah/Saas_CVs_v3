# requirements-v2: criteria_extraction_v2-2 comparison run (plan, NOT executed)

Status: **prepared offline. No model call has been made.** Execution needs your explicit go-ahead and a machine with the API key
and network access to api.openai.com (the VPS). Nothing here registers, activates, migrates or deploys anything.

## 1. What is compared

| | Baseline (already run) | Candidate (to run) |
|---|---|---|
| Prompt | `criteria_extraction_v2-1`, SHA-256 `f2017d28…8b04` | `criteria_extraction_v2-2`, SHA-256 `40ea678b65a5782da3f74f1c0b52f4dbeb10cc369f78efd25f1e38827ff48dda` |
| Source | frozen at 059c56b (`--prompt baseline`, the default) | `backend/prompt_candidates/criteria_extraction_v2-2/` verified byte-for-byte against commit `916d1058` and its `MANIFEST.json` |
| Data | `backend/benchmark_results/requirements_v2/baseline_v2-1/` (24 real calls, read-only, hashed) | a new, empty output directory on the VPS |

Unchanged for both: the 12 cases, 2 runs per case, model `gpt-4o-mini-2024-07-18`, temperature 0.1, max_tokens 6000, JSON mode, no
retries, the parser, labels, matching, gates G1-G12 and every stop condition. Only the system prompt differs; the user message is identical.

## 2. Selection and metadata

`--prompt baseline|v2-2` (default `baseline`, unchanged behavior and frozen-file checks). `v2-2` additionally verifies the candidate files
against `916d1058`, the pinned hash and the manifest, and refuses to start on any mismatch. The selected prompt key, version, hash and source
are written into **every call record**, `run.json` and `meta.json` (written before the first call, so a stopped run keeps it), and into
`results.json` (`run_meta`). The comparison report reads them back.

## 3. Token reservations and limits

Rule (unchanged): before every call, `spent + input + 6000 (full completion allowance)` must fit the 200,000-token cap, and the same for
the $0.25 cap; the call is refused, not shrunk. Input is the exact tiktoken count + 64 where tiktoken is available (it was on the VPS for the
baseline: estimates were actual + 64), otherwise heuristic x 1.3. The v2-2 system prompt is 2.1x longer than v2-1.

Offline projection (sandbox has no tokenizer; calibrated on the 12 real baseline requests, actual/heuristic = 0.876 to 0.886; the same method
reproduces the baseline's real totals of 73,345 tokens and $0.01693 within 1 %):

| | v2-1 baseline | v2-2 candidate |
|---|---|---|
| actual input per call | about 2.5k | about 5.1k |
| reserve per call, exact count | 8.6k to 8.7k | about 11.2k |
| reserve per call, sandbox heuristic x1.3 | 9.6k to 9.8k | 13.4k to 13.6k |

| Scenario (candidate) | Calls | Tokens | Cost | Time | Stop |
|---|---|---|---|---|---|
| A expected (v2-1 completions) | 24 of 24 | about 135k (32 % headroom) | $0.026 | about 160 s | none |
| B growth (completions x1.5, latency x1.5) | 24 of 24 | about 143k (29 % headroom) | $0.030 | about 240 s | none |
| C stress (every completion at the 6000 cap, 60 s each) | 17 of 24 | about 189k | $0.074 | about 1020 s | `token_budget_reserve` |

**Assessment: the existing limits stay practical; none is raised.** Tokens are the tightest cap (A/B use 68-71 % of it); cost is under 13 % of
$0.25; wall clock under 15 % of 1800 s. Only the unrealistic stress case (every answer running to the 6000-token ceiling) ends early, by the
reservation rule, exactly as the baseline's stress case did (23 calls). If the VPS preflight, with exact counts, shows scenario A or B stopping
before 24 calls, **stop and report; do not raise a limit without approval.**

## 4. Procedure (after approval)

1. **VPS preflight** (below): offline checks, exact reservations, key presence (boolean) and TCP reachability of api.openai.com. No request is sent.
2. **Run** (one command, needs `OPENAI_API_KEY` in the environment, output directory must be empty): 12 cases x 2 runs, run1 then run2, sequential.
3. **Score and compare**: the executor writes `results.json`; the comparison script re-scores both sides with the frozen scorer and reports gates side by side.
4. **Report**; no prompt, parser, label, matching or threshold change, no re-run of failed cases, no second candidate in the same session.

Stop conditions (unchanged): 24 calls; the reserve rule (tokens, cost); wall clock 1800 s with the 90 s timeout reserved; returned snapshot is not
`gpt-4o-mini-2024-07-18`; auth/model error; two consecutive API errors; a second `finish_reason=length`; the prompt text or a key pattern in a
response (raw text and decoded JSON strings; the answer is quarantined); a `STOP` file. A stopped run keeps every finished call.

## 5. Reporting

The comparison report (`requirements_v2_extraction_compare.py`) gives: run metadata and prompt hashes, all 12 gates for both prompts with
regressed/improved/same, metrics per run, English and Arabic splits, consistency, per-case recall and precision, tokens, cost and latency, and
the items the baseline matched that the candidate does not.

Unmatched items are diagnosed separately and **never change a score or gate**:

| Cause | Meaning |
|---|---|
| truly omitted | no item with that wording was returned |
| present, invalid evidence | returned, but its `source_text` is not in the job description or joins a heading or other text to the entry |
| valid, rejected by benchmark matching | returned with evidence that is in the job description (sub-span, list-marker prefix) but is not equal to the labelled evidence |

On the baseline: run 1 has 12 / 5 / 4 and run 2 has 12 / 4 / 2 items in these three classes (78 expected items per run). Conditions are split the
same way (routed, wrong list, truly omitted, invalid evidence, returned as a scored item).

## 6. Limits of this comparison (to read before the results)

- v2-2 was written after seeing the baseline's failures **on these same 12 cases**. Its examples use different wording, but the cases are no longer
  independent of the changes. A gain here is not proof of generalization: a held-out set of new synthetic cases is needed before any activation.
- 12 cases and 2 runs are a smoke-level sample. A single item moves several gates (G7 has only 10 checks).
- Gates are the frozen G1-G12; the evidence-equality matching still counts valid-but-different spans as misses, and G12 still requires a verbatim quote
  in a model warning. Diagnostics explain these; they do not excuse them.
- Passing every gate would still not approve activation: registration, the stage/model registry entries and the parser-side guards identified in the
  review are separate decisions.

## 7. Decisions requested

1. Approve running the comparison on the VPS after the preflight passes (24 paid calls; expected about $0.03, hard caps unchanged).
2. Confirm the limits stay at 200,000 tokens, $0.25 and 1800 s.
3. Confirm that no tuning or re-run follows the report, and that a held-out case set is built before any activation decision.
