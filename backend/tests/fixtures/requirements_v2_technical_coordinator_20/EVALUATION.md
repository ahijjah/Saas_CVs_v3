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
**5 calls each**, 10 calls in total. The request is the JD only: the job-context lines of the live request
(title, department, location, …) are not in the stored evidence, so they are not reproduced. That is a limitation.

## Cost

Worst case per call (input at most half the characters, output at max_tokens): v2-3 USD 0.005806, v2-4 USD 0.005905.
Total worst case **USD 0.0586** against the **USD 0.10** cap. The executor refuses to start if the worst case exceeds the
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

Expected: HEAD = pinned commit; FROZEN_OK; sha256 values as in the table above; dry-run total 0.0586; KEY_PRESENT; the
active row is reported (if it is not version 3 with hash 21a2f942…, the report states that the v2-3 arm is the file, not the active row).

Run (owner, from the same worktree; writes only to `--out`):

```bash
OPENAI_API_KEY=… python3 backend/scripts/requirements_v2_tc20_eval.py --execute --out <outside-frozen-paths>/tc20_run_$(date +%Y%m%d)
```

Outputs: `manifest.json` (labels sha, JD sha, prompt shas, plan), `calls.jsonl` (raw answers, finish reasons, usage, cost,
errors), `scored.json` (run totals and the per-arm report).

## Limitations

- One JD; five calls per arm; no tokenizer offline, so input tokens are estimated for the cap, not counted.
- The request omits the live job-context lines (not in the evidence).
- The VPS active prompt row cannot be read from here; the preflight reads it.
- The offline tests use synthetic answers written from the labels; they prove the scorer and the cap, not model compliance.
