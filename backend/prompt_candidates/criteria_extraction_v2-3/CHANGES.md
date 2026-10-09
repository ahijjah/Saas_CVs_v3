# criteria_extraction_v2-3 (offline candidate)

Status: **candidate only**. Not registered in `ai_prompts`, not activated, not wired (not selectable in the executor), not run against any model.
SHA-256 `21a2f9420c12e1a43579c6817b6a8601547d92b653cd432a693ef590b784b43b`. Base: `criteria_extraction_v2-2` (`40ea678b…dda`). `criteria_extraction_v2-1`
(`f2017d28…8b04`), v2-2, the stored results, the frozen parser, the guards and the official benchmark are unchanged.

Scope: (1) OR/AND item structure, (2) routing of conditions and information. Rules 2, 3, 5, 7, 8, 10, 11, 12, 13 (omission, importance, categories, duties,
weights, scoreability, language, conflicts) and the untrusted-text paragraph are byte-identical to v2-2. The JSON shapes and enums of the output format are identical.

## Contradictions and gaps found in v2-2

| # | v2-2 | v2-3 |
|---|---|---|
| 1 | Rule 4 mentioned only a stand-alone "و"; its own Arabic example already used an attached one ("وPowerPoint"). | Rule 4 states the attached "و" (and that a "و" inside a word such as "وظيفة" is not a conjunction). |
| 2 | The item-count rule was spread over rule 4, rule 6 (silent on OR) and the schema (no entry count, no "otherwise null"). | One "Item count" rule in rule 4; a one-line note under ITEM; rules 1 and 6 point to it; experience OR is explicit (one experience item, subject + alternatives). |
| 3 | Rule 4 listed examples of OR only; no rule for "A, B or C", for mixed wording or for experience in "X or Y". | Lists ending in "or" = one item; comma lists without "or" and "and" lists = separate items; mixed = the or-group is one item; unclear grouping follows the wording and is noted in warnings. |
| 4 | CONDITION showed one `category` enum for three lists and never said the label is not the list; the stored v2-2 answers wrote list names as labels (5 times) and filed a benefit in post_hiring_conditions (2). | A note under CONDITION and a table in rule 9: every label maps to exactly one list; the label is never a list name; "other" follows the closest label. |
| 5 | Rule 9 listed routing in prose; "medical" in a benefit could be read as a medical check. | The table states that a benefit mentioning medical or health cover is still a benefit; medical_check is a health examination required of the candidate. |
| 6 | Example 2 filed "the library opens six days a week" as company information while rule 9 sent "work schedule" to non_scoreable_requirements. | The example sentence is now unambiguous company information; rule 9 names "the employer's own opening hours" as company_description. |

v2-2's rule-4 example "Excel and Power BI" carried benchmark wording; the new count lines do not.

Preferred approach: focused clarification (about +2.6 KB, about +640 estimated tokens); no new full worked example. The count lines use unrelated vocabulary and never
use benchmark wording ("Python or Java", "CSS and HTML", clinic/hotel appear only in the offline tests as inputs).

## Size
v2-1: 10,688 B, ~2,661 tokens. v2-2: 22,593 B, ~5,590. **v2-3: 25,185 B, ~6,234** (heuristic estimate of `scripts/requirements_v2_extraction_eval.py`; the exact
tiktoken count is unavailable offline). v2-3 vs v2-2: +2,592 B, +644 tokens (+11.5 %).

## What the offline tests prove, and what they do not
`tests/test_requirements_v2_prompt_candidate_v23.py` proves: the text is internally consistent; only rules 1, 4, 6, 9, two guidance lines and one example sentence differ
from v2-2; the count lines, the routing table and the examples agree with each other, with the real parser, the split-OR guard and a routing checker; every answer in
those tests is written by the tests. They do **not prove model compliance**: nothing here shows that gpt-4o-mini (or any model) follows the instructions. That needs a new,
separately approved benchmark run.
