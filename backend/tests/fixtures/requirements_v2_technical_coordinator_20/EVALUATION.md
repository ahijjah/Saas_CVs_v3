# Bounded evaluation: criteria_extraction_v2-3 vs criteria_extraction_v2-4 on JOB-2026-0121

Status: **prepared and tested offline. Not executed.** Approved within a USD 0.10 cap; to be run from the isolated
benchmark worktree only, by the owner, with the preflight below. Nothing here activates a prompt, changes a job,
migrates the database or deploys.

## What is compared

| Arm | Prompt | SHA-256 | Status |
|---|---|---|---|
| v2-3 | `prompt_candidates/criteria_extraction_v2-3/criteria_extraction_v2-3.txt` (text of the migration-108 prompt) | `21a2f942…b784b43b` | the reference |
| v2-4 | `prompt_candidates/criteria_extraction_v2-4/criteria_extraction_v2-4.txt` | `dd2651bc…b1989a9e` | offline candidate |

Both arms: the exact stored JD (`evidence.json`, sha `c0c7132c…d265`), model `gpt-4o-mini-2024-07-18` (requested and
returned must match), temperature 0.1, max_tokens 6000, `response_format` json_object, timeout 90 s, **0 retries**,
**5 calls each**, 10 calls in total. The user message is the live builder's output for the VPS job context
(`Job Title: Technical Coordinator 20`; department, seniority, location, employment type and work mode are empty on the VPS and
are left out, as the live builder leaves out empty fields), then the verbatim JD block. Both arms send the identical user
message. The run manifest stores the context hash (`context_sha256`) and the user-message hash (`user_message_sha256`).

## Cost

Worst case per call (input at most half the characters, output at max_tokens): v2-3 USD 0.00581, v2-4 USD 0.005909.
Total worst case **USD 0.058595** against the **USD 0.10** cap. The executor refuses to start if the worst case exceeds the
cap, and before each call it refuses if spent plus the worst case of every remaining call would exceed the cap. Measured
cost is recorded per call; the stored answer used 6485 input and 962 output tokens (about USD 0.0016).

## Frozen labels and the mapping (resolves D1–D10 vs eight checks)

`expected_labels.json` (sha256 `6a87da80…67135ef9`) is frozen before the run. Ten definite findings are scored by eight
checks; each finding maps to exactly one check, and each check to its finding(s). A test enforces the mapping and that
each check names an existing regression test.

| Finding | Category | Check | Regression test (xfail today) |
|---|---|---|---|
| D1 16 duty lines as from_responsibilities items | omissions | C_RESP | test_every_responsibility_is_an_experience_item |
| D2 business applications / support processes line | omissions | C_COMP | test_every_competency_line_is_extracted |
| D3 testing / troubleshooting / issue tracking line | omissions | C_COMP | (same) |
| D4 user communication / issue resolution line | omissions | C_COMP | (same) |
| D5 experience OR alternatives | alternatives | C_EXP_OR | test_experience_or_options_are_alternatives |
| D6 familiarity OR alternatives | alternatives | C_FAM_OR | test_familiarity_or_options_are_alternatives |
| D7 local knowledge in domain_knowledge | categories | C_LOCAL | test_local_business_knowledge_is_domain_knowledge |
| D8 work location as non-scoreable | routing | C_LOCATION | test_work_location_is_non_scoreable |
| D9 reporting statements as informational | routing | C_REPORTING | test_reporting_arrangements_are_informational |
| D10 communication / coordination / teamwork split | AND splitting | C_AND | test_and_list_of_soft_skills_is_split |

Ambiguous items A1–A9 (JavaScript wording, soft-skill groupings, category of the familiarity and competency lines,
the second "or" list, the reporting-line reading of the coordination statement) are **not pass/fail**. They are reported
as observations in the run, because the prompt does not settle them.

## Reporting

For each arm the report is kept separate:
- **omissions**: C_RESP and C_COMP, pass count out of 5;
- **alternatives**: C_EXP_OR and C_FAM_OR;
- **categories**: C_LOCAL;
- **routing**: C_LOCATION and C_REPORTING;
- **AND splitting**: C_AND;
- **invented items**: items whose source text is not verbatim in the JD, and from_responsibilities items whose source text is not a JD duty line (counts; no pass/fail);
- **warnings**: number of model warnings per call (counts only);
- **consistency**: per check, `always` / `mixed` / `never` over the 5 calls; invalid-JSON count; item-count range.

## The 12-case benchmark (offline regression only)

`--offline-regression` replays the stored v2-2 answers of the 12 benchmark cases through the official scorer and gates,
read-only. It reproduces the stored gates exactly. Some stored gates are already false (G1, G8, G10, G12); they are
reported as they are. No gate is changed, and the benchmark is not re-run by this evaluation.

## Decision rule (proposed; to be approved before the run)

v2-4 is considered further only if, over the 5 calls, C_COMP and C_RESP pass in at least 4 calls, and the invented-item
count does not rise above v2-3's. One JD and five calls cannot establish compliance; a passing result would justify a
larger approved evaluation, not activation.

## Preflight (VPS, read-only; run from the isolated benchmark worktree)

Pinned commit: **to be filled in after the commit that adds this file and the executor** (see the report).

```bash
cd <benchmark-worktree>
git rev-parse HEAD                                    # must equal the pinned commit
git diff --quiet 059c56b -- backend/services/requirements_v2 backend/tests/fixtures/requirements_v2_benchmark backend/scripts/requirements_v2_extraction_eval.py && echo FROZEN_OK
sha256sum backend/prompt_candidates/criteria_extraction_v2-3/criteria_extraction_v2-3.txt backend/prompt_candidates/criteria_extraction_v2-4/criteria_extraction_v2-4.txt
python3 backend/scripts/requirements_v2_tc20_eval.py --dry-run | tail -4      # no network
test -n "$OPENAI_API_KEY" && echo KEY_PRESENT          # the value is never printed
# read-only: the active DB prompt (report it; do not change it)
psql "$DATABASE_URL" -tAc "SELECT version, encode(sha256(convert_to(system_prompt,'UTF8')),'hex') FROM ai_prompts WHERE prompt_code='criteria_extraction_v2' AND is_active"
```

Expected: HEAD = pinned commit; FROZEN_OK; sha256 values as in the table above; dry-run total 0.058595; KEY_PRESENT; the
active row is reported (if it is not version 3 with hash 21a2f942…, the report states that the v2-3 arm is the file, not the active row).

Run (owner, from the same worktree; writes only to `--out`):

```bash
OPENAI_API_KEY=… python3 backend/scripts/requirements_v2_tc20_eval.py --execute --out <outside-frozen-paths>/tc20_run_$(date +%Y%m%d)
```

Outputs: `manifest.json` (labels sha, JD sha, prompt shas, plan), `calls.jsonl` (raw answers, finish reasons, usage, cost,
errors), `scored.json` (run totals and the per-arm report).

## Failure handling and the one-request diagnostic (added after the HTTP 400 at bb08f9f)

- The run stops at the first failed call (any error, not only HTTP 400 or authentication). No call is retried, and no later call is attempted.
- For each failure only these are kept: the exception class, the HTTP status and the provider's `code`, `type` and `param` (identifiers only; anything else is dropped). The provider message, headers, the API key and the request content are never stored.
- A run with no successful call is reported as `unavailable`; no check is reported as passing or consistent.
- `--out` must be a new or empty directory. Existing evidence is never overwritten.
- `--diagnose --arm v2-3|v2-4 --out <new dir>` makes exactly ONE request, the exact evaluation request of that arm, under its worst-case reservation (must fit the cap). It writes `diagnostic.json` with the request settings and message hashes (no content), and the answer or the sanitized error. It does not retry.

## Control experiment: v2-3 without the EXAMPLES block (prepared, NOT run)

Prompt `criteria_extraction_v2-3-noex` (`backend/prompt_candidates/criteria_extraction_v2-3-noex/`, sha256 `764d2ee4ad8a523e67ef27b143b41d01538853d8f7d3c8921f3adf4430b87d90`
— see its MANIFEST.json): the v2-3 text with only the worked-examples block removed. It is an exact byte prefix of v2-3
before the EXAMPLES heading, so the rules and the JSON output contract are byte-identical; the removed span is pinned.

- Same JD, same VPS job context and user message (sha256 `ff6b334a…`), same model and settings, five calls, no retries,
  stop at the first failed call, sanitized errors, new-or-empty output directory, the frozen labels and the frozen scorer.
- Education-alternative retention (the JD's degree sentence keeps its alternatives) is reported separately and is not an
  official check. The five stored v2-3 answers are read offline for comparison (all five retained the alternatives).
- Dry-run (no network): worst-case total USD 0.02484 for five calls, under the USD 0.03 cap for this control:

```bash
python3 backend/scripts/requirements_v2_tc20_noexamples.py --dry-run            # no network (default)
```

- The paid run is `--execute --out <new directory>` with `OPENAI_API_KEY`. It has NOT been run and is not approved by this
  section. A result can show whether the examples contribute to the stored failures; it cannot show that they are the sole cause.

## Stronger-model comparison (prepared offline, NOT run; pricing recorded)

Audit of the no-examples run: `audit/AUDIT_NOEX.md` (provenance, reproduced scores, raw-answer comparison, parser handling).

- Arm: the **unchanged full v2-3 prompt** (sha256 `21a2f942…b784b43b`), the same stored JD, the same VPS job context and user
  message (sha256 `ff6b334a…240473`), the frozen labels (`6a87da80…`) and the frozen scorer.
- Recommended model: **`gpt-4.1-2025-04-14`** (a pinned snapshot). Reasons: a chat model that accepts the same four
  request settings as the stored arm (temperature 0.1, max_tokens 6000, json_object, timeout 90), so the model is the only
  intended change. Not chosen: the newer GPT-5-family models, because reasoning models may reject some of these settings
  (I could not confirm their parameter rules here).
- **Pricing and snapshot (verified, source recorded):** `gpt-4.1-2025-04-14` at standard input **$2.00** and output **$8.00** per 1M tokens, from https://developers.openai.com/api/docs/models/gpt-4.1 (owner-supplied reading of that page; the sandbox could not open it). The first request checks that the returned model is the pinned snapshot; a mismatch stops the run. The paid path also requires `--out` and `OPENAI_API_KEY`.
- Budget: cap **USD 0.40** for five calls. Worst case (input estimated as characters/2, output at 6000 tokens):
  **USD 0.387340** (0.077468 per call). The stored gpt-4o-mini calls used about 6,485 prompt and about 1,000 completion tokens
  each, so the expected cost is far below the cap; the cap is on the worst case.
- Protocol: five calls, no retries, the run stops at the first failed call or on a returned model that differs from the
  pinned one, the cap guard runs before each call, sanitized errors only, a new or empty output directory.
- Reporting: category accuracy (the official checks by category), raw weight validity, education-alternative retention and
  schema compliance are reported in separate groups. None of them replaces or changes a stored score.
- Disclosed differences from the stored arm: the model (the purpose); the reservation uses the standard prices recorded in PRICING_STATUS; the
  characters/2 input estimate overstates tokens.

Dry-run (no network, the default):

```bash
python3 backend/scripts/requirements_v2_tc20_compare.py --dry-run
```

Paid run (not approved by this section; the owner's approval and the verified prices come first):

```bash
OPENAI_API_KEY=… python3 backend/scripts/requirements_v2_tc20_compare.py --execute --out <new directory outside frozen paths>
```

## Candidate comparison: full v2-3 (control) versus criteria_extraction_v2-5 (prepared offline, NOT run)

- Candidate: `backend/prompt_candidates/criteria_extraction_v2-5/` (sha256 `f1569a8b400257db20b1c0b24fd7728605fe472eef7fff993a384f12f0cbc8db`; see its MANIFEST.json and DIFF.patch). Built from the full v2-3 text by the diff listed in CHANGES.md; the output contract is byte-identical.
- Same model for both arms: `gpt-4.1-2025-04-14`, temperature 0.1, max_tokens 6000, json_object, timeout 90, no retries, stop at the first failed call or on a returned model that differs.
- Evaluation units and calls per arm:
  - TC20 (frozen labels and official checks): 5 calls per arm.
  - `eval_en_field_service_01` (English, new): 3 calls per arm.
  - `eval_ar_facilities_01` (Arabic, new): 3 calls per arm.
  - **Total: 22 calls** (11 per arm). The new JDs and their expected outputs are frozen in `tests/fixtures/requirements_v2_candidate_v25/eval_set/` (MANIFEST.json hashes); the expected outputs were fixed in the commit that adds this section, before any call.
- Budget: worst case **USD 1.706582** (chars/2 input estimate, 6000 output tokens per call), cap **USD 2.00**. The worst case overstates the real cost, which for the stored stronger-model run was USD 0.0294 per call.
- Groups, reported separately and never merged into one score:
  - omissions (expected items and conditions not found; TC20 C_RESP and C_COMP);
  - categories (TC20 C_LOCAL);
  - OR/AND structure (incomplete or spurious alternatives, AND items merged or given alternatives; TC20 C_EXP_OR, C_FAM_OR, C_AND);
  - importance (expected importance differs);
  - routing (conditions missing or in the wrong list; TC20 C_LOCATION, C_REPORTING);
  - invented content (source text not verbatim in the JD, and duties not in the JD's duty list);
  - extra items (verbatim items that match no expected entry).
- Education-alternative retention is not an official group; it is reported as in the earlier runs.
- Every run manifest records: the git commit, the sha256 of the run script, the comparison script, the TC20 scorer, the candidate, the control, the eval-set manifest, the labels, the pricing status and the plan.

Dry-run (no network, the default):

```bash
python3 backend/scripts/requirements_v2_candidate_eval.py --dry-run
```

Paid run (not approved by this section):

```bash
OPENAI_API_KEY=… python3 backend/scripts/requirements_v2_candidate_eval.py --execute --out <new directory outside frozen paths>
```

## Limitations

- One JD; five calls per arm; no tokenizer offline, so input tokens are estimated for the cap, not counted.
- The job context is the VPS's title only; the other fields are empty on the VPS, so the message matches the live one.
- The VPS active prompt row cannot be read from here; the preflight reads it.
- The offline tests use synthetic answers written from the labels; they prove the scorer and the cap, not model compliance.
