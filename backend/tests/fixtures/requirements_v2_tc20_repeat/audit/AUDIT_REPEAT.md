# Audit of the TC20 repeat (v2-3 versus v2-5): status and static analysis

## Status: the four repeat files were NOT available, so the run audit is not done

The request named the attached files `calls.jsonl`, `manifest.json`, `run.json` and `scored.json`. None was attached to the
message, none is in the uploads folder, and a search of the filesystem found no repeat outputs (the only matches were this
session's own temporary test directories, which are not evidence). Steps 1 to 4 therefore cannot be performed, and nothing in
this document reports a repeat result.

Needed to complete the audit: the four files, re-attached, byte for byte. Once they are stored under
`tests/fixtures/requirements_v2_tc20_repeat/run/`, the reproduction tests in `test_requirements_v2_tc20_repeat_audit.py` run
without skipping, and this document gets its results section.

## What was done without the files (no paid call, no change to any score, label, artifact or setting)

### 5. The exact v2-3 → v2-5 diff (static)

The diff (`prompt_candidates/criteria_extraction_v2-5/DIFF.patch`) has four hunks. Verified with the offline tests:

| Part | Changed? | Relevance to the three defects |
|---|---|---|
| Output contract (OUTPUT FORMAT, ITEM, CONDITION) | No | — |
| Rule 9 (routing of reporting, location, company description) | No, byte-identical | Reporting routing text is unchanged |
| Rule 4, OR sentence ("options joined by or are one requirement") | No, but a paragraph is appended: "Complete alternatives …" | Strengthens OR; cannot by itself remove alternatives |
| Rule 4, AND sentence (comma list is separate items) | No. A counting line is added: "Communication and teamwork skills = 2 items, both soft_skills" | The bundled TC20 line is not this phrase (see below) |
| Rule 5, `skills` and `soft_skills` | Yes, rewritten | Moves the soft items to `soft_skills` (observed) |
| New COVERAGE paragraph (before rule 1) | Added (~1.2 KB) | Does not name reporting (checked) |
| Importance paragraph in rule 3 | Added | Item-level importance only |
| Example 5 (warehouse JD, appended after Example 4) | Added (3,222 characters) | The only worked example with an informational `reporting_line` item |

Facts about the text (checked by tests):
- The phrase "Strong communication, coordination" is in neither prompt. The bundled TC20 item is therefore not copied from an example.
- "Communication and teamwork" appears in v2-5 only, in the new counting line and in Example 5, and both show the split form.
- Prompt size: 23,810 characters (v2-3) to 29,055 (v2-5).

Hypotheses (not established; none tested):
- **Reporting disappearing.** Candidates: (a) Example 5 as the only example with an informational reporting item, which may change what the model does with reporting statements; (b) the COVERAGE pass, which asks for every sentence to become an item or condition without naming reporting; (c) the longer prompt. Nothing in the diff removes or reroutes a reporting line, so the rules alone do not explain it.
- **OR alternatives disappearing (C_EXP_OR, C_FAM_OR).** The OR rule text is unchanged except for an appended paragraph that asks for complete alternatives. The stored answers already lost these alternatives under v2-3 in some runs, so this is at least partly inherited from v2-3. No diff text removes them.
- **Communication, coordination and teamwork remaining bundled (C_AND).** The AND rule (comma lists are separate items) is unchanged, and the new counting line is about "and", not the comma list. Moving the items to `soft_skills` without splitting them is the observed pattern. The soft-skill rule in v2-5 defines the category, not the split.

### 6. Observed facts versus causal hypotheses

Observed in the stored paired run (run `v2-3_v2-5_20261010`, see `audit/AUDIT.md`): v2-5 omitted the reporting statement from the whole answer in 4 of 5 calls, and its `informational_items` were empty in those calls; v2-3 omitted it in 1 of 5. The C_AND and OR checks failed in both arms.

Not established by any run so far: the cause of the reporting omission; whether it is reproduced in the repeat (the repeat files are not available here); whether the change in the prompt or the run-to-run variance of gpt-4.1 at temperature 0.1 is responsible. A repeated regression across two paired runs is consistent with a real effect but does not identify a cause.

### 7. Proposed minimal controlled experiment (NOT run; no budget committed)

- **Single change:** delete Example 5 (the warehouse JD and its answer, from "Example 5 (EN" to the end of the file) from `criteria_extraction_v2-5`. Every other byte stays the same.
- **Why this change:** it is the smallest textual change that removes the only new reporting item, and it is the easiest to revert. Removing the COVERAGE paragraph instead is a larger change and would also remove the coverage pass the candidate depends on.
- **Arms:** A = v2-5 as it is (the existing candidate, the reference for this question); B = v2-5 without Example 5. The control v2-3 is not needed for the question "does Example 5 cause the reporting omission inside v2-5", and would add 5 calls.
- **Calls:** 5 per arm, 10 in total, alternating A, B, A, B, … (the same protocol as the repeat). Same model, settings, JD, job context and user message. No retries; stop at the first failure or unexpected returned model; a new or empty output directory.
- **Budget:** worst-case reservation about $0.83 (the same estimator as the repeat, slightly lower for B because its prompt is shorter). Proposed hard cap $1.00.
- **Checks:** all eight TC20 checks per call and per arm (frozen scorer); reporting statement present in the whole raw answer; `informational_items` count; duty completeness (of 16). OR and AND reported separately, as in the repeat protocol.
- **Decision rules (fixed before the run):**
  1. Example 5 is supported as the cause of the reporting omission only if B has three or more calls with the statement present and A has one or fewer, in the same run.
  2. If B still shows four or more calls missing, Example 5 is not the cause; the next single change would be the COVERAGE paragraph (not run).
  3. Any other pattern is reported as inconclusive. Ten calls cannot establish a cause; the result is evidence for or against one hypothesis.
- **Status:** not approved, not built, not run. v2-5 stays inactive.
