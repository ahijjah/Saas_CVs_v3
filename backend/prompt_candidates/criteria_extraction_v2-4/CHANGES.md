# criteria_extraction_v2-4 (offline candidate)

Status: **candidate only**. Not registered in `ai_prompts`, not activated, not run against any model.
SHA-256 `dd2651bcda14ae816c89d33dd08ecb4aefa5d63e2e51dfbe7d2b39f8b1989a9e`. Base: `criteria_extraction_v2-3` (`21a2f942…b784b43b`).

## Why (evidence: JOB-2026-0121, see tests/fixtures/requirements_v2_technical_coordinator_20)

The stored v2-3 answer omitted every duty of the Responsibilities section, three explicit competency lines, the
alternatives of two "or" lists, and the work-location and reporting statements, and bundled one AND list, while the
rules for each already existed in v2-3. Whether the prompt or the model caused these omissions is not known; the
bounded evaluation measures it. This candidate only adds general wording that targets the failure modes.

## Changes (four passages; everything else is byte-identical to v2-3)

| # | Where | v2-3 | v2-4 |
|---|---|---|---|
| 1 | RULES, new paragraph before rule 1 | none | COVERAGE pass: walk every section, heading, list entry and sentence in both languages; each must become an item or a condition; do not summarise; not written into the output. |
| 2 | Item count, after the "Experience follows the same rule" line | counting rule for skills and experience | the same counting rule for competencies and abilities under a skills/competencies/abilities/qualifications heading; each sentence of such a list is covered by at least one item. |
| 3 | Rule 7, last sentence | duty extraction under any heading | every duty line in every group of a duties section, including sub-headed groups, is extracted; no group is skipped because a later heading follows. |
| 4 | Rule 9, first sentence | three lists by label table | places of work and reporting, supervision, submission and communication-about-progress statements are conditions even inside a responsibilities section. |

No example was added. Wording was checked against this JD and the 12 benchmark cases: none of their distinctive terms appears in the candidate (the offline test enforces this).

## Not changed (on purpose)

The output format block, the JSON shapes and enums, rules 2, 3, 5, 6, 8, 10, 11, 12, 13, the untrusted-text paragraph, the CONDITION block and the ITEM block.
Categories are not re-defined: the category question for the local-knowledge line is left to the existing rule 5 (domain knowledge = subject-matter knowledge).

## Risks

- Length grows by about 1.3 KB (about +330 estimated tokens). The cost bound of the bounded evaluation already covers it.
- The COVERAGE pass may make the model output more items (over-extraction). The evaluation's invented-item check and the consistency check measure this.

## Inherited overlap (not introduced by v2-4)

The v2-3 text (inherited unchanged) contains illustrative examples whose phrases overlap with benchmark JDs (for example
the "Bachelor's degree in" sentences and a note addressed to an AI system). The overlap is recorded by a test
(`tests/test_requirements_v2_tc20_eval.py::test_inherited_overlap_is_recorded`). The leakage test covers only the text
that v2-4 adds; the added wording shares no five-word phrase with any JD or benchmark case.
