# Human review of the staged-validation checker: reviewer instructions

Status: **offline, no judgments filled.** Every reviewer choice cell is blank. No precision, recall or agreement is reported here, because
none exists until reviewers have filled the sheets. Keep seeded results separate from real-answer results (see the sheets and
`seeded_defect_results.csv`).

## 1. What the checker does, and what this review asks

The checker (`scripts/requirements_v2_staged_check.py`) reads a job description (JD) and one stored answer in the existing output contract.
It quotes back each item and condition's `source_text` to a JD statement, lists statements no quotation covers, checks each condition's list
against the rule 9 table, and flags possible lost OR alternatives and possible bundled AND items. It does not decide any of these.

Each row asks one question: **does the answer have the defect the row names?** The row also shows the exact JD statement, the matching
extracted item (if any), the checker's warning and its rationale.

## 2. Split of the cases

- **unseen** (`review_sheet_unseen.csv`): the 12 benchmark cases B01 to B12. The checker's code was not changed in response to them, and their
  expected results were not used to tune it. **They are not unseen in the strict sense:** while designing this package the author ran the
  checker on them and saw the warning counts, and chose the sampling and the probes after doing so. No other held-out set exists. The answers are the stored v2-2 prompt run 1, produced by **gpt-4o-mini-2024-07-18**, not gpt-4.1. Run 2 of the
  same cases is not sampled.
- **dev** (`review_sheet_dev.csv`): TC20 and the English and Arabic eval job descriptions. The checker's tests and seeded mutations were built on
  these. Answers: two TC20 answers (routing run, CONTROL call 1; scope run, CLARIFIED call 2) and the v2-3 and v2-5 call 1 answers for each eval JD,
  all gpt-4.1. Treat dev results as development evidence only.
- **seeded** (`seeded_defect_results.csv`): mutations of the English and Arabic reference answers. Detection here is **by construction**. These
  rows are not reviewed and are not evidence about real answers. Two rows are documented gaps and are recorded as `known_gap = yes` with
  `detected = False`: an attached Arabic و (not detected), and a bare unpunctuated requirement line that the answer omits (silent by
  construction; only a heading-candidate row can show it). A bare line the answer does quote is detected as `source_is_heading`.

## 3. Sampling (what is and is not in the sheets)

- One answer per unseen case and six dev answers. The TC20 answer with all sixteen responsibility lines missing is **not** included; the checker's
  behaviour on it is pinned by its tests.
- Punctuation-only partial quotes: the sheets list **one** per answer. The other such warnings in the same answer are counted in
  `MANIFEST.json` (`punctuation_only_partial_not_listed`) and are not in the sheet. Every warning class is otherwise listed in full.
- Heading candidates and probes that repeat the same JD statement across answers of one case appear once; the answer labels are joined.

## 4. Row kinds

| row_kind | Meaning |
|---|---|
| checker_warning | A warning the checker actually raised (uncovered background or omitted text, partial quote, wrong list, unknown or other category, possible lost OR, possible bundled AND, ambiguous or non-verbatim quote, and so on). |
| heading_candidate | A line the checker classed as a heading: not a bullet, no terminal punctuation, at most eight words. It may be a title or section label, or a requirement the checker did not extract. |
| location_probe | A condition in the location category, with the list the answer placed it in. Rule 9 assigns location to non_scoreable_requirements. |
| checker_silent_probe | Something the checker does **not** warn about but that this package asks you to look at: an OR statement with no whole-statement quotation (the lost-alternatives test did not run), an OR statement quoted by two or more items
  (rule 4 keeps the options as one item), and an attached Arabic و. |

## 5. Columns

- `review_id`, `split`, `case_id`, `language`, `answer_label`, `row_kind`, `warning_type`: identify the row.
- `jd_statement_exact`: the JD statement, copied exactly. `(no span in the job description)` when the checker found no span.
- `matched_item`: `path | category or list | text` of the extracted item or condition that the checker linked to the statement, or `none`.
- `matched_source_text`: the item's quoted source, as written in the answer.
- `warning_text`: the checker's message. For probes, the reason the probe was added.
- `rationale`: what the warning type means (fixed text).
- `mechanical_note`: a **fact** about the row, not a judgment. Examples: "quote equals the statement without its terminal punctuation",
  "quote omits the label before the colon", "partly quoting item keeps no alternatives", "placed in non_scoreable_requirements".
- `reviewer_choice`: **fill with one of** `genuine defect`, `false alarm`, `ambiguous`. Leave blank if unsure, and use `ambiguous` then.
- `reviewer_note`: optional, one line.

## 6. How to choose

For **checker warnings**:
- *genuine defect*: the answer has the defect the warning names. The warning is correct about the answer.
- *false alarm*: the answer does not have that defect. The warning is wrong.
- *ambiguous*: the JD or the rule allows both readings, so the warning cannot be settled from the text.

For **probes** (location, silent OR, attached و, duplicate) the choice describes the **answer**:
- *genuine defect*: the answer has the defect the row names (for example, a location in the wrong list, an OR list that should have been kept,
  an AND pair that should have been split, a duplicate item).
- *false alarm*: the answer is fine on that point.
- *ambiguous*: both readings are defensible.

For **heading candidates**:
- *genuine defect*: the line is a requirement, duty or condition that the checker did not extract.
- *false alarm*: the line is a title or section label and is correctly a heading.
- *ambiguous*: it could be either.

## 7. What the checker cannot detect (not in any sheet)

- **Item category** (for example experience versus skills). The checker does not judge categories of items. Wrong item categories are pinned as a gap.
- **Importance** (Required or Preferred) beyond the cue check. Not checked.
- **Experience fields** (subject, minimum years) beyond the presence of the object. Not checked.
- **Alternatives content**. The checker checks only that an alternatives list has two or more entries when present, and that OR text has alternatives.
  It does not check that the alternatives are the right options.
- **OR statements split across items** are probed, not warned about. The checker does not count the items per statement for OR.
- **Whether a statement is a requirement, a duty, a condition or context.** Uncovered statements may be any of these. Only the reviewer decides.
- **Weights and warnings** in the answer. Not checked.
- **Attached Arabic و** is detected only as a standalone token. Attached forms are probed, not warned.

## 8. Checker limitations found while building this package (mechanical, from the data)

- Partial-quote warnings are dominated by mechanical differences. The counts for every warning in the 12 unseen answers are in `MANIFEST.json`
  under `partial_quote_mechanical_classes`. Most partial quotes lack only the statement's terminal punctuation, or omit a label before a colon.
- The lost-OR and bundled-AND tests run only on statements a quotation covers in full. A quotation that lacks the final punctuation therefore
  switches those tests off. The silent-OR probes show this case.
- An OR statement split across items, each keeping the alternatives, is not warned about. Attached Arabic conjunctions are not warned about.

These are facts about the checker's behaviour on these answers. They are not claims about how often real reviewers would agree.

## 9. Returning the sheets

Fill `reviewer_choice` and optionally `reviewer_note`. Do not change other columns. Keep the sheet's file name. Two reviewers should work
independently if possible; agreement will be computed only after both have filled the sheets, and not from this repository.
