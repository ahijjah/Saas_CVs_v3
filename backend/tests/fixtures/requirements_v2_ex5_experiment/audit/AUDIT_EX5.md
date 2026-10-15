# Audit of the Example 5 removal experiment (commit 4ea4323): verified

Status: **provenance verified; costs verified; the saved report reproduced exactly by the unchanged scorer; whole-answer reporting
checked; the experiment kept separate from earlier runs.** No paid call, no prompt, label, scorer, frozen artifact or production
setting was changed. v2-5 stays inactive.

Evidence: `run/` (the four attached files, byte for byte).

| File | sha256 |
|---|---|
| `calls.jsonl` (10 rows) | `2cff7b1a333262897ec8402119883b0fa3821cdb045175bc94cf2f2727a1262e` |
| `manifest.json` | `246e67d4b8542632e9c187365d1e7d25cfabc7001f71e15fe05b0eafaee70bcc` |
| `run.json` | `2dab3f683d63f5fe6036415b164624694795bbbe22b9bdb0c4e11fb5e996a1d9` |
| `scored.json` | `b09f593dc2968f5964d3d6e036efb2afdea744ceb3ed332a86bb160432385a34` |

## 1. Provenance

- Manifest: git commit `4ea4323b206f6e32307c902d72d65e5c2a1bbff9`; protocol `5a7412438a42cdc50f4e41b847031584a1c1a0de34438eeea274650abad853be`; FULL = `f1569a8b…` (`criteria_extraction_v2-5`); VARIANT = `7cf171c8…66e62` with removed span `33bdd1dc…e31e6` (3,223 bytes); experiment script `1211e876…36c2c`; user message `ff6b334a…240473`; labels `6a87da80…`.
- Current code: every pin equals the current file, except `git_commit`, which is the commit this audit was run against. The experiment script, protocol, variant, candidate, labels and scorers are byte-identical between `4ea4323` and `HEAD` (checked with `git diff`).
- Order: FULL 1, VARIANT 1, FULL 2, … (ten calls, alternating, FULL first) as planned.
- Every row: system hash equals its arm's pin; user hash equals `ff6b334a…`; model returned `gpt-4.1-2025-04-14`; `finish_reason` `stop`; no error.

## 2. Costs

| Arm | Per-call cost (USD) | Arm total |
|---|---|---|
| FULL (calls 1–5) | 0.040774, 0.044134, 0.043294, 0.043310, 0.043294 | 0.214806 |
| VARIANT (calls 1–5) | 0.039474, 0.039418, 0.039418, 0.039402, 0.039482 | 0.197194 |

The ten costs sum to **0.412000** USD, equal to `run.json` and the report. The reservation in the manifest is **0.81101** (exact), under the USD 1.00 cap.

## 3. Exact reproduction

`requirements_v2_ex5_experiment.report(rows, labels, jd)`, with the unchanged TC20 labels and scorer, returns the saved `scored.json` report **in full**, including the per-call records, the fresh-arm summaries, the direct comparison, and the historical context.

## 4. Reporting, checked against the whole raw answer

The labelled check uses one sentence ("submit deliverables"). The JD's reporting paragraph has three reporting sentences, and each was searched
in every raw answer, not only in `informational_items`.

| Order | Arm | Call | Reporting sentences in the whole answer (of 3) | Where they are | Labelled check |
|---|---|---|---|---|---|
| 1 | FULL | 1 | 0 | absent (no informational items) | fail |
| 2 | VARIANT | 1 | 0 | absent | fail |
| 3 | FULL | 2 | 3 | all three in `informational_items`, category `reporting_line` | pass |
| 4 | VARIANT | 2 | 0 | absent | fail |
| 5 | FULL | 3 | 3 | all three in `informational_items`, `reporting_line` | pass |
| 6 | VARIANT | 3 | 0 | absent | fail |
| 7 | FULL | 4 | 3 | all three in `informational_items`, `reporting_line` | pass |
| 8 | VARIANT | 4 | 0 | absent | fail |
| 9 | FULL | 5 | 3 | all three in `informational_items`, `reporting_line` | pass |
| 10 | VARIANT | 5 | 0 | absent | fail |

- The whole-answer check agrees with the saved score in all ten calls.
- Where FULL keeps the reporting statement, it keeps all three sentences and routes them correctly. Where it is absent, it is absent from the whole answer. No reporting text was found in any other list or category.
- So the failure is an **omission**, not a misrouting, and the calls that do include the statement route it correctly under rule 9.

## 5. Other groups, reported separately

- **Duty coverage:** 16 of 16 in every call of both arms.
- **OR alternatives:** the ICT list kept in FULL call 2 and VARIANT call 5 (1 of 5 each). The familiarity list kept in FULL call 2 only (FULL 1 of 5; VARIANT 0 of 5).
- **AND splitting:** the communication, coordination and teamwork line is one item and is in `soft_skills` in all ten calls. It is never split (0 of 10).
- **Frozen checks, fresh arms:** C_RESP 5/5 both; C_COMP 5/5 both; C_LOCAL 5/5 both; C_LOCATION 5/5 both; C_REPORTING FULL 4/5, VARIANT 0/5; C_EXP_OR FULL 1/5, VARIANT 1/5; C_FAM_OR FULL 1/5, VARIANT 0/5; C_AND 0/5 both.

## 6. Interpretation (the result as the data support it)

- **Removing Example 5 did not improve reporting or OR/AND handling.** Reporting went from 4 of 5 (FULL) to 0 of 5 (VARIANT), a difference of
  −4 in the same batch. The OR and AND results were the same or worse. The result is not an improvement.
- **This weakens the hypothesis that Example 5 causes the omission.** It does not establish causation, and it does not eliminate interactions
  with other instructions or run-to-run variance. With five calls per arm, the design cannot distinguish a large effect from variation.
- **FULL's reporting result varies substantially across runs.** The same prompt (FULL, `f1569a8b…`), model and user message give:

| Run (same prompt, same model) | FULL: reporting present (of 5) |
|---|---|
| Paired 22-call run, commit `1e73231` | 1 |
| TC20 repeat, commit `23cce8e` | 0 |
| Example 5 experiment, commit `4ea4323` | 4 |

  Across the three runs, 5 of 15 FULL calls contain the statement. The variation is larger than the difference the experiment was designed to
  detect. The v2-3 arm in the same runs contains it in 9 of 10 calls (paired 4 of 5; repeat 5 of 5).

- **OR alternatives for v2-5 across all four v2-5 arms:** ICT kept in 2 of 20 calls (paired 0/5, repeat 0/5, experiment FULL 1/5, VARIANT 1/5);
  familiarity kept in 1 of 20 (0, 0, 1, 0). v2-3 keeps the ICT list in 4 of 10 repeat-and-paired calls (paired 0/5, repeat 4/5).

Historical runs are reported separately above and are not pooled into the fresh arms for the comparison.

## 7. Corrections to the earlier status

The earlier status document said the reporting result was unverified and described the coverage wording as a "contradiction" between COVERAGE and rule 9.
Both points are corrected in `AUDIT_STATUS.md`:
- The experiment's numbers are now verified (above).
- The COVERAGE wording is a **possible scope ambiguity**, not a proven contradiction. Rule 9 still explicitly requires routing: "Each statement goes in exactly ONE of three lists." The verified data show that when the model includes the reporting sentences it routes them correctly, which is consistent with the omission lying in what the model decides to carry forward, not in rule 9's routing. That is a hypothesis about scope, not a demonstrated cause.
