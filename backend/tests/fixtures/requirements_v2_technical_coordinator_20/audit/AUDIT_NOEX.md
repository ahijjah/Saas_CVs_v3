# Audit: the no-examples control run (criteria_extraction_v2-3-noex, gpt-4o-mini-2024-07-18)

Status: **provenance verified; scores reproduced with the unchanged scorer; raw answers compared with the stored v2-3 baseline.**
No paid call was made in this audit. No prompt, label, scorer, frozen artifact, production setting or cap was changed.

## 0. Provenance

Evidence is in `audit/run_tc20_v2-3-noex/` (copies of the uploads, byte for byte):

| File | sha256 | Upload |
|---|---|---|
| `calls.jsonl` (5 rows) | `28632bd29e407bc0b04716ca3ea258f8bfa5ffec185658815c19619460e3239b` | `62483b2a-calls.jsonl` |
| `manifest.json` | `d0e7bd71bce08e6c97a6c59f536277b85d638582cd452d79b2256e4c5d907047` | `02b1eaac-manifest.json` |
| `scored.json` | `344f837487da06f868278fe3fe53824a39e7e9df43cab702524f0b33bc326d43` | `4d78a4ba-scored.json` |

Checks performed:

- The manifest's prompt sha256 is `764d2ee4…87d90` (the no-examples variant) and its base sha256 is `21a2f942…b784b43b` (v2-3); the removed-span sha256 `71e72b12…2c322` matches the commit that added the variant.
- Every call's `input.system_sha256` equals the variant hash and `input.user_sha256` equals `ff6b334a…240473` (the stored VPS user message). The labels sha256 `6a87da80…` matches.
- All five calls succeeded (no errors), each with `finish_reason: stop` and the requested model `gpt-4o-mini-2024-07-18`. The run cost USD 0.007568 against the USD 0.03 cap; the dry-run reservation was USD 0.02484.
- Reproduction: `requirements_v2_tc20_noexamples.report(calls, jd, labels)` run with the frozen scorer returns exactly the report in `scored.json` (`test_requirements_v2_tc20_compare.py` asserts equality).
- Raw answer hashes (`analysis.json` and below): calls 2 and 4 are byte-identical, and calls 3 and 5 are byte-identical; so the five calls contain **three distinct answers**.

## 1. Scores (reproduced, unchanged scorer)

| Check | No-examples (5 calls) | Stored v2-3 baseline (5 calls, gpt-4o-mini) |
|---|---|---|
| C_RESP (duty lines) | 0/5 | 0/5 |
| C_COMP (competency lines) | **5/5** | 0/5 |
| C_EXP_OR | 0/5 | 0/5 |
| C_FAM_OR | 0/5 | 0/5 |
| C_LOCAL | 0/5 | 0/5 |
| C_LOCATION | **0/5** | 3/5 |
| C_REPORTING | 0/5 | 0/5 |
| C_AND | 0/5 | 0/5 |

The only gain is C_COMP. The loss is C_LOCATION (3/5 to 0/5): no call put the work location into `non_scoreable_requirements`.
Education-alternative retention (reported separately, not an official check): 0/5 here; 5/5 in the stored baseline.

## 2. Raw answers against the stored baseline

Items per answer are counted from the raw JSON (before the parser).

| Observation | No-examples (5 answers) | Stored baseline (5 answers) |
|---|---|---|
| All three competency lines extracted | **Yes, 5/5**, all in `skills` | No, 0/5 (the lines are absent) |
| Category weights total | **300 in all 5** (skills 100, experience 100, education 100; others 0) | 100 in all 5 |
| Soft skills in `soft_skills` | **0 items**; the same competency wording sits in `skills` (11 skills items in every answer) | 7 items in `soft_skills` |
| JavaScript / web knowledge | **In `experience`** (preferred, "is considered an advantage") | In `skills` |
| Arabic and English merged | **Yes, 5/5**: one skills item "Good command of Arabic and English, including …" | Yes, 5/5 (the same merge; not a change) |
| Education alternatives | **Absent, 5/5** (`alternatives: null` on the degree item) | Present, 5/5 (Computer Science / Information Technology / Software Engineering / Information Systems / related field) |
| Duties (`from_responsibilities`) | **0 in all 5** | 0 in all 5 |
| Condition lists (non-scoreable, post-hiring, informational) | **Empty in all 5** | Non-scoreable filled in calls 2, 3 and 5 (3, 4 and 3 items; this is where the location statement sits for C_LOCATION); empty in calls 1 and 4; post-hiring and informational empty in all 5 |
| Local business knowledge (C_LOCAL) | In `experience`, not `domain_knowledge` (all 5) | Not in `domain_knowledge` either (C_LOCAL 0/5) |

Note on the 300 total: rule 11 says the weights "do not need to total exactly 100" and each must be a whole number 0 to 100. A total of 300 is therefore **within the stated contract**, not a violation. It is a weighting choice: three equal categories, with no weight on soft skills or domain knowledge. The parser turns it into a valid split (section 4). The defect is what the weights sit on, not their sum.

Note on the three categories: the no-examples answers use the same three-category split in all five answers (`skills` 11, `experience` 5, `education` 1). Soft-skill wording was moved from `soft_skills` into `skills`, which is a category-assignment defect (rule 5 and the category list), not a parser problem.

## 3. Schema compliance (raw, against the output contract)

| Code | Count (no-examples, 5 answers) |
|---|---|
| `item_missing:alternatives` | 16 items (skills and experience items with no `alternatives` key; calls 1: 6, 2: 5, 4: 5) |
| `item_missing:experience` | 2 items (the education item in calls 2 and 4 has no `experience` key) |

Compliant answers: 2 of 5 (calls 3 and 5). The stored baseline is compliant in 5 of 5. Every other top-level key, the seven category arrays, the enums, the weights and the condition lists are present and valid in all five answers.

The missing keys are **raw-model defects**: the contract lists all seven item keys with null where empty.

## 4. How the existing parser handles these outputs (offline, `services.requirements_v2.extraction.parser.process_ai_output`)

Evidence: `audit/run_tc20_v2-3-noex/parser_outcome.json` (produced by a one-off offline command, not a committed script, to keep the production package out of the scripts directory).

- Status `draft` for all 5 answers; no errors.
- **Missing item keys are accepted silently**: an absent `alternatives` becomes `None`, an absent `experience` becomes `None`. No review issue is raised, so the recruiter is not told. (The stored baseline raised `source_text_not_found` for 3 answers; the no-examples answers raised none, because their quotes are verbatim.)
- **Weights are normalized, not rejected**: the 100/100/100 proposal is applied as `skills 34, experience 33, education 33` (largest remainder, sums to 100). The stored 100-total proposal was applied as `30 / 40 / 15 / 15` for skills, experience, education and soft skills.
- **Categories are not re-assigned**: soft-skill wording in `skills` stays in `skills`; JavaScript in `experience` stays in `experience`. No parser check exists for these choices.
- **Items: 17 kept per answer**, `unmapped` 0 in every answer; the conditions are empty, as the raw answers are.

Parser corrections are therefore limited to weight normalization and to the silent defaulting of missing optional keys. Everything in section 2 reaches the recruiter as the model wrote it.

## 5. Raw-model defects versus parser corrections

| Kind | Item |
|---|---|
| Raw-model defect | Competency wording placed in `skills` instead of `soft_skills` (all 5) |
| Raw-model defect | JavaScript / web knowledge placed in `experience` (all 5) |
| Raw-model defect | Education alternatives dropped (all 5) |
| Raw-model defect | Location statement not routed to `non_scoreable_requirements` (all 5); reporting statement also not routed |
| Raw-model defect | Duties not extracted (all 5); condition lists empty |
| Raw-model defect | Missing `alternatives` (16) and `experience` (2) keys |
| Contract-compliant, not a defect | Weights totalling 300 (rule 11) |
| Parser correction | Weight normalization to 100 (34 / 33 / 33) |
| Parser correction | Missing optional keys read as `None`, silently |

## 6. What this run shows, and does not show

- Removing the example block co-occurred with C_COMP rising (0/5 to 5/5) and with C_LOCATION (3/5 to 0/5) and education-alternative retention (5/5 to 0/5) falling. The effects are mixed, and one JD with five calls cannot separate the example block from the other variable (the run-to-run variation of the model). No causal claim is made.
- Only one JD and two models were measured. The five no-examples calls contain three distinct answers, so the consistency of a single prompt is limited.
- The stored and no-examples runs used the same model. Whether a stronger model removes the defects in section 5 is the question of the next, prepared, comparison (`scripts/requirements_v2_tc20_compare.py`); it is not answered here.

## 7. Next step (proposed, not run)

A controlled comparison of the **unchanged full v2-3 prompt** on a stronger pinned model, with the same JD, context, labels and scorer. See `EVALUATION.md`, section "Stronger-model comparison". Its prices and snapshot are not yet verified against the official OpenAI pages, so its paid path refuses to start until they are.
