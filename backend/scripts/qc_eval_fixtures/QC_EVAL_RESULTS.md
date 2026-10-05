# Qualifying-context evaluation — results and decisions (audit log)

Evaluation-only record for `experience.qualifying_context` extraction. Nothing here is wired into production:
the production job-analysis prompt, `services/ai_service.py`, D-01, S1, S2, the frontend and the database are
unaffected by this evaluation.

Harness: `backend/scripts/qc_context_eval.py` · Fixtures: `qc_main_cases.json` (qc-main-1, 34 cases),
`qc_heldout_cases.json` (qc-heldout-1, 21 cases) · Prompts: `prompts/`.

---

## 1. candidate_qc-1 — MAIN real evaluation

| Item | Value |
|---|---|
| Fixture | qc-main-1 (34 cases) |
| Prompt | candidate_qc-1 |
| Prompt SHA-256 | `fc979dd4a48b2aa5922c87f62e613700f898da13599e43e62a4fbc1304c851df` |
| Model / temperature | gpt-4o-mini / 0.2 (max_tokens 200, response_format json_object) |
| Input | JD text only, in the prepared user wrapper |
| Runs | 34 cases × 5 runs = 170 real calls |
| Failed technical runs | 0 |
| Harness commit | `9d720f3` (real mode guarded by `--confirm-real`) |

Figures below are as reported from the server run's `results.json` / `report.md`; the run artefacts themselves
are not committed to the repository.

### Overall

| Metric | Value |
|---|---|
| pass_rate | 0.9647 |
| state_accuracy | 1.0 |
| false_qualifying_context | **0** |
| missed_qualifying_context | 0 |
| identified_precision | 1.0 |
| identified_recall | 1.0 |
| uncertain_accuracy | 1.0 |
| grounding_accuracy | 1.0 |
| grounding_failures | 0 |
| context_accuracy (strict) | 0.9368 |
| invalid_outputs | 0 |
| decoy_hits | 0 |
| stability | 33/34 cases; modal agreement 0.9941 |

All predefined MAIN acceptance gates (hard and split gates) **PASS**.

### By language

| Language | state_accuracy | context_accuracy |
|---|---|---|
| Arabic | 1.0 | 1.0 |
| English | 1.0 | 0.92 |

### Known MAIN variances (both are context-span issues; no state, grounding or safety failure)

1. **QC-C2 — span-boundary variance (1/5 runs).**
   JD: "At least 5 years of experience in payroll processing within government entities."
   Expected (one_of): "government entities" / "within government entities".
   4/5 runs returned "government entities"; run 3 returned "payroll processing within government entities".
   The extra words are the function the context restricts, not a different scope: verbatim, grounded, state
   correct. Classified as a span-boundary variance, correctly scored as a strict context miss. This is the
   single unstable case behind 33/34 stability.

2. **QC-I1 — contiguous phrase split into two AND contexts (5/5 runs).**
   JD: "…as an Internal Auditor in Islamic banking institutions in the GCC region."
   Expected one contiguous context: "Islamic banking institutions in the GCC region" (or the leading-preposition
   variant). All 5 runs returned `["Islamic banking institutions", "GCC region"]`.
   Under the frozen same-experience AND semantics (every context must hold for the same experience) the split
   is semantically equivalent here, because "in the GCC region" is an intersective (locative) restriction. It
   correctly remains a strict context-format miss: candidate_qc-1 explicitly says not to split qualifiers that
   stand together in one phrase, and splitting is not safe in general (it would turn an OR phrase into AND, and
   non-intersective splits change meaning; equivalence also depends on AND being applied per experience, not
   across the whole profile).

## 2. Decision — FREEZE candidate_qc-1

- **candidate_qc-1 is frozen** at SHA-256 `fc979dd4a48b2aa5922c87f62e613700f898da13599e43e62a4fbc1304c851df`
  (byte-pinned in `backend/tests/test_qc_context_eval.py`).
- The prompt and the fixtures are **not** changed on the basis of the observed MAIN results.
- Strict `context_accuracy` and the evaluation canonicalisation are **not** loosened before held-out.
- QC-C2 and QC-I1 are recorded above as known MAIN variances.
- Context decomposition (accepting splits of a contiguous phrase) may be studied later **only as a separate
  diagnostic metric**, defined before any run, never replacing strict `context_accuracy`, and never accepting a
  split across "or" / "أو".
- **Family N** (context-only requirement, e.g. a preferred-experience sentence with no years or role): today's
  experience enumeration produces no criterion for it. This gap remains known and out of current scope.
- **Held-out (qc-heldout-1) has NOT been exposed to any model or run.** It is to be run once, against the frozen
  prompt, as a separately approved step (`--allow-heldout` is required by the harness).

## 3. results.json record shape (for diagnostics)

`<out>/results.json` = `{"meta": {...}, "summary": {...}, "records": [...]}`; one record per case and run.
QC records differ from the S1 boundary-harness records (which use `case` and a nested `checks` object).

| Field | Meaning |
|---|---|
| `case_id` | case identifier (there is no `case` key) |
| `run` | run number (0-based) |
| `raw` | raw model response text; `None` only for a technical failure |
| `parsed` | strictly parsed `{state, contexts, source}`, or `None` if invalid / technical failure |
| `error` | parse/validation error (`invalid_json`, `bad_keys`, …), `failed_technical`, or `None` |
| `technical_error`, `finish_reason`, `usage`, `response_model` | API-call diagnostics |
| `state`, `contexts` | the answer's state and contexts as scored |
| `expected`, `expected_state` | the fixture's expected labels |
| `valid`, `state_ok`, `consistency_ok`, `grounding_ok`, `ungrounded`, `context_ok`, `candidates_ok`, `decoy_hit`, `pass` | check outcomes — **flat top-level fields; there is no nested `checks` object** |
| `prompt_version`, `prompt_sha256`, `model`, `temperature`, `fixture_version`, `family`, `lang`, `split` | run and case metadata |

Example (read-only) extraction:

```
python3 -c "import json;d=json.load(open('<out>/results.json'));[print(r['case_id'],r['run'],r['raw'],r['state_ok'],r['grounding_ok'],r['context_ok']) for r in d['records'] if not r['pass']]"
```
