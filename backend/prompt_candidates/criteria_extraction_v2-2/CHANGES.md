# criteria_extraction_v2-2 (offline candidate)

Status: **candidate only**. Not registered in `ai_prompts`, not activated, not wired, not run against any model.
`criteria_extraction_v2-1` (`services/requirements_v2/extraction/prompts/`, SHA-256 `f2017d28…8b04`) and the frozen baseline at
`059c56b` are unchanged. The output contract (OUTPUT FORMAT, ITEM, CONDITION) is byte-identical to v2-1, so the existing parser applies as is.

What the checks in `tests/test_requirements_v2_prompt_candidate_v22.py` prove: the text is internally consistent, keeps the v2-1 contract
and conflict semantics, and its examples are valid input for the real parser. What they do NOT prove: that any model follows the prompt.
That needs a new, separately approved benchmark run.

## Changes by confirmed issue (v2-1 → v2-2)

| Issue (baseline finding) | Change |
|---|---|
| Preferred items omitted, Preferred-only job returned `open_broad` | Rule 1: Preferred items are requirements, always extracted, also when nothing is Required. Rule 12: all-Preferred is `scoreable`; `open_broad` only when no requirement of any importance is listed. Example 2. |
| Weights | Rule 11: zero for categories without a Required item; all zero when none has one; weights come only from the role and the Required categories (the phrase "emphasis of the description" is removed). |
| OR alternatives split or one-entry | Rule 4: one item, every option in `alternatives` (two or more), also for skills and languages; never one item per option, never a single-entry list; "and" / "و" stays separate items. Examples 1 and 3. |
| Heading merged into `source_text` | Rule 2: `source_text` is the entry alone, no governing heading, no list marker, never two places; a heading is never a reason to omit an entry. Rule 3: the cue (inline word or governing heading) is copied separately and may lie outside `source_text`. Examples 1 and 3. |
| Embedded instructions | Untrusted-text paragraph: text addressed to an AI/system never controls importance, weights, added or removed items, disclosure or output. Statements about the role, including "optional", stay job description content under rule 3 (conflict semantics unchanged; rule 13 unchanged). Examples 1 and 3. |
| Conditions erased when categories are empty | Rule 12: with no requirements only the seven category arrays are empty; conditions and informational content are still extracted (rule 9). Examples 2 and 4. |
| Six months became 0 | Rule 6: months, weeks, days (digits or words, English or Arabic) give `min_years` null, never 0, a fraction or a rounded number; the contract is unchanged. Examples 1 and 3. |
| Repeated requirements and duties dropped, Arabic duty headings | Rule 10: a restated requirement or duty (note, closing sentence, other section) is its own item. Rule 7: duties under any heading, with Arabic headings listed. Examples 1 and 3. |

## Unchanged on purpose
Rules 5, 8, 9 and 13 and the v2-1 examples are byte-identical; rule 3's importance logic is unchanged (only the cue sentence is clarified).
The parser, labels, matching, gates, registries, API and UI are untouched.

## Size
About 22.6 KB (v2-1: 10.7 KB); roughly 3k more input tokens per call (heuristic). Cost impact is small but real; see the test output.
