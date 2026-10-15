# Human review of the staged-validation checker, v2: reviewer instructions

Status: **offline, no judgments filled.** Every reviewer choice cell is blank. This package reports no precision, recall, agreement or
production readiness. Unit tests and the starter sample do not establish any of these.

## 1. What this review asks

The checker reads a job description (JD) and one stored answer in the existing output contract. It traces each item and condition to a
verbatim quotation, lists statements the answer does not cover, checks each condition's list against the rule 9 table, and flags candidate
OR and AND defects and candidate attached Arabic conjunctions. It decides none of these. Each row asks: **does the answer have the defect
the row names?** Each row shows the exact JD statement, the matched item (if any), the checker's warning and its rationale.

## 2. Status of the cases: all development evidence

Every case in this package has been inspected during development: the twelve benchmark cases (B01 to B12, six English and six Arabic), TC20,
and the English and Arabic eval JDs. **None is an independent, held-out or unseen validation set.** The benchmark answers come from the v2-2
prompt run with gpt-4o-mini-2024-07-18; TC20 and the eval answers come from gpt-4.1 runs. Run 2 of the benchmark cases is not included.

## 3. The two sheets

- `review_sheet_v2.csv`: every checker warning and probe for the stored answers.
- `starter_review_sheet_v2.csv`: twelve rows, chosen by the fixed rules in section 7 (two English and two Arabic per stratum). The starter is a
  sample to test the instructions and the choices. It is not a basis for any measure.

## 4. Row kinds

| row_kind | Meaning |
|---|---|
| checker_warning | A warning the checker raised. Includes uncovered statements, shortened quotations, wrong lists, unknown or other categories, possible lost OR, possible OR split, possible bundled AND, possible attached AND, source-is-heading and ambiguous heading lines. |
| location_probe | A location-category condition, with the list the answer placed it in. |
| checker_silent_probe | Something the checker does not warn about: an OR statement that no item quotes at all, or an Arabic word beginning with و that the conservative pattern does not match. |

Heading-shaped lines (no terminal punctuation, not bullets) that no quotation covers appear as `ambiguous_heading_line` warnings. They are
**not** counted as omissions.

## 5. Columns

`review_id`, `source_group`, `case_id`, `language`, `answer_label`, `row_kind`, `warning_type`: identify the row. `jd_statement_exact`: the JD
statement, copied exactly. `matched_item`: the path, category or list and text of the linked item or condition, or `none`.
`matched_source_text`: the quotation as written in the answer. `warning_text`: the checker's message. `rationale`: what the type means (fixed).
`mechanical_note`: a **fact** about the row, not a judgment (for example, "quote omits the label before the colon" or "partly quoting item keeps
no alternatives"). `reviewer_choice`: fill with one of `genuine defect`, `false alarm`, `ambiguous`. `reviewer_note`: optional, one line.

## 6. How to choose

For **checker warnings**:
- *genuine defect*: the answer has the defect the warning names. The warning is correct about the answer.
- *false alarm*: the answer does not have that defect. The warning is wrong.
- *ambiguous*: the JD or the rule allows both readings, so the text cannot settle it.

For **probes**, the choice describes the **answer**: *genuine defect* if the answer has the defect the row names (a location in the wrong list,
an OR list that should have been kept, a missed Arabic conjunction), *false alarm* if the answer is fine on that point, *ambiguous* if both readings
are defensible.

For **ambiguous heading lines**: *genuine defect* if the line is a requirement, duty or condition the answer did not extract; *false alarm* if it is
a title or section label that is correctly not extracted; *ambiguous* if it could be either.

## 7. Starter sheet: selection rules (fixed before the sample was drawn)

The sheet is sorted by case, then by review identifier. From that order, each stratum takes its first two English rows and its first two Arabic
rows.

1. **real_warning**: checker warnings of type wrong_list, unknown_category, possible_lost_or, possible_or_split, uncovered_statement or
   partial_quote. These are the warnings a reviewer is most likely to confirm.
2. **candidate_warning**: candidate-type warnings that may be false alarms, in the priority possible_bundled_and, possible_attached_and,
   ambiguous_heading_line, source_is_heading, then sheet order.
3. **unflagged_probe**: checker_silent_probe and location_probe rows, which may hide a defect the checker did not warn about.

## 8. What the checker cannot detect (not in any sheet)

- **Item category** (for example experience versus skills): not judged.
- **Importance** (Required or Preferred) beyond the cue check: not checked.
- **Experience fields** beyond the presence of the object: not checked.
- **Alternatives content**: only that alternatives hold two or more entries and that OR text has alternatives.
- **Whether a statement is a requirement, a duty, a condition or context**: an uncovered statement may be any of these.
- **Attached Arabic conjunctions** other than the conservative pattern (before ال or a Latin letter): not checked. Examples: وتحتاج, وإجازة.
- **The AND rule for duty lines**: AND candidates are raised only for stated requirements, not for duty lines (origin from_responsibilities), to avoid
  flagging lists of objects. A duty that joins two tasks with و is not raised.
- **Weights and warnings** in the answer.

## 9. Checker limitations (mechanical)

- A quotation that omits only the final punctuation, or a label before a colon, covers its statement. The mechanical note says so.
- Quotations from separate items that together cover a statement's substantive text, with only separators, whitespace or the conjunction
  و or "and" between them, are recorded as a split across items. They are not a partial quotation.
- A quotation, or a set of quotations, that leaves a substantive word unquoted remains a partial quotation, and the warning names the unquoted text.
- An OR statement quoted by two or more items is a candidate, and nothing is merged.
- Attached Arabic و is matched only before ال or a Latin letter. This is conservative: some conjunctions are missed (section 8).

## 10. Returning the sheets

Fill `reviewer_choice` and optionally `reviewer_note`. Do not change other columns. Keep the file names. Two reviewers should work
independently if possible. Agreement is computed only after both have filled the sheets, and not from this repository.
