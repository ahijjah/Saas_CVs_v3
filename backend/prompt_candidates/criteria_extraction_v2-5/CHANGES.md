# criteria_extraction_v2-5 (offline candidate)

Status: **candidate only**. Not registered, not activated, not run against a model.
SHA-256 `f1569a8b400257db20b1c0b24fd7728605fe472eef7fff993a384f12f0cbc8db`. Base: `criteria_extraction_v2-3` (`21a2f942…b784b43b`), full text.

## Why (confirmed remaining failures, stored runs)

- Duties were extracted in 2 of 5 calls of the stronger model and in none of 5 gpt-4o-mini calls on the same JD: the
  coverage of sub-headed duty groups is not explicit.
- Soft skills stayed in `skills` in every stronger-model call (C_AND 0/5): the technical versus behavioural line is not drawn.
- The experience and familiarity "or" lists lost their alternatives (C_EXP_OR 2/5, C_FAM_OR 0/5): the completeness of an OR
  list is not reinforced, and no decision test separates OR from AND.
- Importance was classified per list in some answers: the item-level rule is not stated.

## Changes (the exact diff is DIFF.patch)

| # | Where | Change |
|---|---|---|
| 1 | Start of RULES (new paragraph) | COVERAGE: section-by-section pass; every requirement, duty and competency line becomes an item or a condition; a group of duties under a sub-heading is extracted in full. |
| 2 | Rule 3 (one new paragraph) | Importance is decided item by item: a heading governs only the entries beneath it; an item's own cue wins; no whole-list classification from one entry. |
| 3 | Rule 4 (OR and AND) | Complete alternatives: every option in the job description's order, including a general last option, each as an entry; a decision test OR versus AND; each AND item names only itself. |
| 4 | Rule 4 (count check) | One counting line: "Communication and teamwork skills" = 2 items, both soft_skills. |
| 5 | Rule 5, `skills` | Technical abilities, including those worded "ability to ...", are skills. |
| 6 | Rule 5, `soft_skills` | Behavioural and interpersonal traits only; a technical ability is never a soft skill. |
| 7 | Examples | Example 5 (new, EN, warehouse vocabulary): sub-headed duties, an OR with complete alternatives, AND members as separate items, technical and soft skills apart, a Preferred entry under a heading, conditions. |

Unchanged on purpose: the output contract, the ITEM and CONDITION blocks, rules 1 to 13 (numbers and wording apart from the
insertions above), examples 1 to 4. Examples 1 to 4 were checked against the new rules by the offline tests and are consistent with them.

## Not changed

The untrusted-text paragraph, the condition routing table (rule 9), the weights rule (rule 11), the scoreability rule (rule 12),
and every frozen artifact (v2-1, v2-2, v2-3 and v2-4 files, the parser, the labels and the scorer).
