# Audit of the TC20 repeat (v2-3 versus v2-5, gpt-4.1-2025-04-14, 10 calls)

Status: **provenance verified; the saved report reproduced in full by the unchanged scorer; separate-run comparison; whole-answer
reporting check; diff inspection; observed facts separated from hypotheses.** No paid call was made, and no historical score,
label, frozen artifact, prompt or production setting was changed. v2-5 stays inactive.

Evidence: `run/` (the four attached files, byte for byte, plus `analysis.json` written by `scripts/requirements_v2_tc20_repeat_audit.py --write`).
Tests: `tests/test_requirements_v2_tc20_repeat_audit.py` (23 pass; static checks of the diff, byte identity of the four files, pins, rows, spend, full report, analysis, separated facts).

## 0. Provenance

| File | sha256 |
|---|---|
| `calls.jsonl` (10 rows) | `1c3c9bb12c0c47480dbd015694b9766e11ab0287fd449d605d52c1b19265d9ed` |
| `manifest.json` | `512536540b4b5c4e20f9e425d80b39ea00ff9319df4585512be941625094e3ba` |
| `run.json` | `3177650783e65ffbedb8868db1054d2c97311167b179fc2731a0d74bb22a3527` |
| `scored.json` | `2a3a240885d1d1f477ca8a053c9e0b4047186a019d449497f6903ce64fd50edb` |

- Run commit (manifest): `23cce8e96ce32b88fc462204f4b942a7cf7defba`. The repeat script's sha256 (`2001d0af…`), the protocol (`860eed9e…`), the candidate, the control, the labels and the scorer pins in the manifest equal the current files.
- Every row: system hash = its arm's prompt; user hash = `ff6b334a…` (the stored TC20 user message); model returned = requested `gpt-4.1-2025-04-14`; finish `stop`; no error; cost equals usage at $2/M input and $8/M output.
- Order: v2-3 call 1, v2-5 call 1, … (matches the protocol).
- Spend: 0.391792 USD, equal to the sum of the rows and to `run.json`; cap USD 1.00; planned worst case 0.8009.
- **Reproduction:** the unchanged scorer, applied to `calls.jsonl`, returns the report in `scored.json` in full (including the comparison block).

## 1. The repeat, call by call

| Order | Arm | Call | Failed checks | Duty lines (of 16) | Reporting statement in whole answer | informational_items |
|---|---|---|---|---|---|---|
| 1 | v2-3 | 1 | C_FAM_OR, C_AND | 16 | yes | 2 |
| 2 | v2-5 | 1 | C_EXP_OR, C_FAM_OR, C_REPORTING, C_AND | 16 | **no** | 0 |
| 3 | v2-3 | 2 | C_EXP_OR, C_FAM_OR, C_AND | 16 | yes | 3 |
| 4 | v2-5 | 2 | C_EXP_OR, C_FAM_OR, C_REPORTING, C_AND | 16 | **no** | 0 |
| 5 | v2-3 | 3 | C_AND | 16 | yes | 3 |
| 6 | v2-5 | 3 | C_EXP_OR, C_FAM_OR, C_REPORTING, C_AND | 16 | **no** | 0 |
| 7 | v2-3 | 4 | C_FAM_OR, C_AND | 16 | yes | 3 |
| 8 | v2-5 | 4 | C_EXP_OR, C_FAM_OR, C_REPORTING, C_AND | 16 | **no** | 0 |
| 9 | v2-3 | 5 | C_RESP, C_AND | **0** | yes | 3 |
| 10 | v2-5 | 5 | C_EXP_OR, C_FAM_OR, C_REPORTING, C_AND | 16 | **no** | 0 |

## 2. The two runs, kept separate (never pooled as evidence)

Frozen checks passed, out of 5 calls per arm:

| Check | Paired run: v2-3 | Paired run: v2-5 | Repeat: v2-3 | Repeat: v2-5 |
|---|---|---|---|---|
| C_RESP (duties) | 4 | 5 | 4 | 5 |
| C_COMP | 5 | 5 | 5 | 5 |
| C_EXP_OR | 0 | 0 | **4** | 0 |
| C_FAM_OR | 0 | 0 | 2 | 0 |
| C_LOCAL | 5 | 5 | 5 | 5 |
| C_LOCATION | 4 | 5 | 5 | 5 |
| C_REPORTING | 4 | **1** | 5 | **0** |
| C_AND | 0 | 0 | 0 | 0 |

Observed differences between the two runs of the same v2-3 arm: C_EXP_OR 0 → 4 and C_REPORTING 4 → 5 (the control itself moved). The v2-5 arm was stable on every check except C_REPORTING (1 → 0).

## 3. Reporting omissions, against the whole raw answer

- The labelled check looks for "submit deliverables". The JD's reporting paragraph has three reporting sentences: "submit deliverables …", "in coordination with the MoNE IT focal point …", and "maintain regular communication …". All three were searched in the whole raw answer, not only in `informational_items`.
- **v2-5, repeat:** none of the three sentences appears anywhere in any of the five answers, and `informational_items` is empty in all five. In those answers the JD's work location is still in `non_scoreable_requirements`, and no reporting text is in any other field.
- **v2-5, paired run:** missing in 4 of 5 calls; call 1 contained the statement. Across both runs, v2-5 omits the reporting statement from the whole answer in **9 of 10** calls, and its `informational_items` are empty in 9 of 10.
- **v2-3:** missing in 1 of 10 (paired run call 1, which also lacked the duty lines), present in all 5 repeat calls.
- Classification: an **omission**, not a misrouting. The text is not in another list or category. (Verified in the whole raw answer.)

## 4. Duties, OR alternatives and AND splitting

- **Duties:** 0 of 16 lines in 2 of 10 v2-3 calls (paired call 1 and repeat call 5), 0 of 10 v2-5 calls. This is a control defect. The duty lines are absent from the whole answer (not placed elsewhere).
- **OR alternatives (ICT list, "1–3 years … ICT systems support, business applications, digital platforms, or software implementation projects"):** kept in 4 of 10 v2-3 calls (all in the repeat), and in **0 of 10** v2-5 calls. The familiarity list ("…or enterprise applications") is kept in 2 of 10 v2-3 calls and 0 of 10 v2-5 calls.
- **AND (communication, coordination, teamwork):** the line is a single item in all 20 calls. It is in `skills` in every v2-3 call and in `soft_skills` in every v2-5 call. It is never split.

## 5. The diff, and what could explain each observation

The exact diff (`prompt_candidates/criteria_extraction_v2-5/DIFF.patch`) changes four things: a new COVERAGE paragraph, an item-level importance paragraph in rule 3, an appended "Complete alternatives" paragraph in rule 4 plus a counting line for "Communication and teamwork skills", and the `skills` and `soft_skills` definitions in rule 5. Example 5 is appended. Rule 9, the output contract and the existing OR and AND sentences are byte-identical (tests).

**Reporting omission.** No instruction removes or reroutes a reporting statement. Candidates: (H1) Example 5 is the only worked example with an informational reporting item, and its answer has an empty `informational_items` section for other statements; (H2) the COVERAGE pass asks for everything to be carried, but never names reporting; (H3) the longer prompt (29,055 against 23,810 characters) dilutes attention to the reporting paragraph. None is tested.

**OR alternatives.** The OR sentence is unchanged; only a paragraph is appended. In the v2-5 answers the ICT item sits in `experience` with `alternatives: null`, and the familiarity item sits in `skills` with `alternatives: null`. No instruction in the diff explains the absence. (H4) The added COVERAGE pass or the rule 5 rewrite changes how list items are built; (H5) run-to-run variance. The v2-3 arm kept the list in 4 of 10 calls, so the loss is not a fixed property of the control either.

**Communication bundled.** The comma-list rule in rule 4 is unchanged. The phrase "Strong communication, coordination, and teamwork skills" appears in neither prompt, so it was not copied from an example. The v2-5 split example ("Communication and teamwork skills") is a different phrase. (H6) The soft-skill rule moves the item to `soft_skills` without making the model split it. Not established.

## 6. Observed facts versus causal hypotheses

**Observed and repeated (across the two runs):**
- v2-5 omits the reporting statement from the whole answer in 9 of 10 calls; v2-3 in 1 of 10.
- v2-5 has no ICT OR alternatives in 10 of 10 calls; v2-3 has them in 4 of 10.
- v2-5 has no familiarity OR alternatives in 10 of 10 calls; v2-3 in 2 of 10.
- Neither arm ever splits the communication/coordination/teamwork line (0 of 20).
- Duty lines are missing in 2 of 10 v2-3 calls and 0 of 10 v2-5 calls.

**Observed in one run only:** the v2-3 arm's C_EXP_OR (0 of 5 then 4 of 5) and C_REPORTING (4 then 5). These show that the control is not stable on these checks, so its values cannot serve as a fixed baseline.

**Not established:** which change in the diff causes the reporting omission or the loss of the OR alternatives; whether the regression is caused by the candidate at all rather than by a feature of the prompt family. The repeated pattern is consistent with a real effect of the v2-5 text, but two runs of ten calls do not identify the cause.

**The OR regression is not yet clean.** For the ICT list, v2-5 is 0 of 10 and v2-3 is 4 of 10, so the difference is repeated in direction; but the control's own two runs disagree (0 of 5 then 4 of 5). Five calls per arm cannot separate that from variance.

## 7. Proposed experiment (not built, not run; no budget committed)

- **Single change:** remove Example 5 (the warehouse JD and its answer, from "Example 5 (EN" to the end of the file) from criteria_extraction_v2-5, keeping every other byte. This is the most direct test of H1. It also lets the OR and AND results be read off the same calls.
- **Arms:** A = v2-5 as it is (already stored: 10 calls); B = v2-5 without Example 5. Run as a new pair of 5 calls each, alternating, same protocol, same model, settings, JD, context and user message. A control arm (v2-3) is not included: it would add 5 calls and push the budget over the cap; it is already described by the two existing runs.
- **Budget:** exact worst case 5 × (0.082712 + 0.07949) = **USD 0.81**, against a proposed hard cap of USD 1.00. (B's prompt is 3,222 characters shorter, which lowers its per-call reservation by 0.003222.)
- **Checks:** all eight TC20 checks per call; the reporting sentences in the whole raw answer; `informational_items`; duty completeness; the ICT and familiarity OR alternatives; the communication split. Reported separately.
- **Decision rules, fixed before execution:**
  1. H1 (Example 5) is supported only if B has three or more calls with a reporting sentence in the whole answer, while A (9 of 10 already) stays at one or fewer calls with the sentence present in this run. Otherwise H1 is not supported.
  2. The OR result counts as a change only if B keeps the ICT alternatives in four or more of five calls. Three of five is inside the control's observed variance and is reported as inconclusive.
  3. The communication line is reported only. No decision is made on it from this experiment.
  4. If B still omits the reporting statement in four or more calls, H1 is rejected and the next single change to test would be the COVERAGE paragraph (not run).
- **Limit:** ten calls cannot establish a cause. The experiment can support or reject H1 only in the direction stated above.
