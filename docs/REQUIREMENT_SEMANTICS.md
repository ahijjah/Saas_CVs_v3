# Requirement semantics: how a criterion's wording combines

Job Analysis now captures, per criterion, **how its wording combines**, so scoring, gates and the Candidate Matrix apply the same logic to every role, industry and language. Nothing in the code names a tool, a qualification, an industry or a language: the AI reads the meaning; the application applies a fixed, tested set of rules.

## 1. The AI interprets the job description (prompt `criteria_extraction_v2-4`)

Per item, besides category, importance and the verbatim `source_text`, the AI may return a `logic` block and `also_stated_in`:

| Meaning in the text | Stored as |
|---|---|
| Conditions that must **all** be met | separate items (each its own weight) |
| Alternatives where **any one** is enough | one item, `logic.options` (acceptance `direct`) |
| An alternative made of several conditions | an option with `all_of` (every part must be met for that option) |
| Accepted **only after human review** ("or equivalent, subject to approval") | an option with acceptance `review` |
| Illustrations ("such as", "for example", "e.g.") | `logic.examples`: never requirements, options or items |
| A reading that is genuinely unclear | `logic.ambiguity` (the most literal reading is kept; the doubt is flagged) |
| The same obligation stated twice | one item; the other statements verbatim in `also_stated_in` |
| Statements that differ in a number, level, scope, condition or exception | separate items |

Language requirements and soft skills stay separate items in their own categories. The legacy `alternatives` field mirrors the option texts, so everything that already reads it keeps working.

**What code does with the AI's answer** (`extraction/logic_reading.py`): ids are assigned by code; an option, part or merged statement whose words are not in the job description is not trusted (an option is demoted to *review*; a merged statement is dropped, and reported); an option set with no direct option is reset for review; a merge whose statements differ in a number or carry an exception/condition the first lacks keeps every statement and gets an ambiguity note (a person verifies that no distinct obligation was lost). Nothing is split, dropped or merged silently.

## 2. The recruiter verifies and corrects before approval

Job Analysis shows each criterion's interpretation beside its source wording: *Any one of*, *All of* parts, options *accepted only after review*, examples (marked "not requirements"), merged sources, and a flagged ambiguity. An item the AI flagged as ambiguous is **not settled by the AI's own reading**: it appears under *Structure to confirm* and blocks scoring until a recruiter confirms or corrects it. A new edit operation `set_logic` (admin / HR manager) corrects options, acceptance, parts, examples or the ambiguity note; the change is recorded as a structure correction, source wording and weight are untouched, and invalid input (fewer than two options, no direct option, unknown fields) is refused rather than repaired. English and Arabic/RTL.

## 3. The AI assesses evidence per option; the application combines (`item_scoring/combine.py`)

For a criterion with options the model returns a result, verified quotations, an explanation and a confidence for every **leaf** (an option, or each part of an option that has parts). It never decides how they combine. Code does:

* parts of one option: all met -> met; any not met -> not met; any unclear -> unclear; otherwise partially met (AND)
* the criterion: a **direct** option met -> **met** (the first in document order is the matched option); else a **review** option met -> **unclear** (`conditional_alternative`: never an automatic pass or fail); else any option unclear -> **unclear**; else any partially met -> partially met; else all not met -> not met
* a criterion flagged ambiguous can only be *met* by evidence: a would-be partial/not-met becomes unclear (`ambiguous_requirement`)
* a leaf the model did not assess is unclear (absence of evidence is never "not met"); a leaf whose quotation is not in the CV, or whose confidence is below 0.60, is downgraded to unclear as before

The stored evaluation records the matched option and every option's result (and the part results), so each screen can show **which alternative was met and why a case needs review**.

## 4. The application applies the approved scoring rules

* **One criterion, one weight.** Meeting several options earns nothing extra; merged duplicates are one item with one weight.
* **Gates.** The combined result drives the gate exactly like any item: met passes; confirmed not met / partially met fails; **unclear (ambiguity, missing evidence or a conditional alternative) needs verification, never fails.**
* **Matrix.** The same outcomes appear, with two new review flags (`conditional_alternative`, `ambiguous_requirement`), filters, and the options in the evidence drawer. Candidate scoring and the matrix drawer show the matched option, every option's result and the plain-language reason.
* **Examples are never required**: they are given to the model as illustrations, are not leaves, and cannot be scored.

## Compatibility and migration

* All new item keys (`logic`, `also_source_texts`) are optional. A document without them is exactly what it was: same validation, same digests (readiness basis hash, structure basis hash), same comparison, same evaluation path (one judgement per item), same scores and decisions. Tested against the previous algorithms. Existing approved jobs are not re-extracted, re-confirmed or re-scored.
* Schema version stays 2. The extraction prompt moves to `criteria_extraction_v2-4`; the item evaluation prompt to `item_level_v2-2`. Jobs extracted earlier keep their stored prompt version.
* Migration `105_requirement_logic_evaluations.sql` (additive, idempotent): `application_item_evaluations.matched_option`, `option_results` (NULL for every existing row) and two new `review_reason` labels. Rollback `db/rollback/105_requirement_logic_evaluations.down.sql` (run on a restored copy first) removes the columns and the two labels; results, evidence, scores, runs and gates are untouched. No new table, grant or RLS change.
* Deploy order: apply 105, then the new code. Old code reading 105's schema is unaffected (new columns are ignored).

## Limits and assumptions

* The quality of the AI's *interpretation* depends on the model; offline tests script the interpretation and verify what the code does with it (and the E2E suite uses a scripted model). A real-model check on unfamiliar job descriptions is part of the staging checklist.
* Option wording is checked against the job description by word coverage (75 %, with Arabic article and English plural normalization); a faithful paraphrase passes, an invented option is demoted to review. A very loose paraphrase may be demoted unnecessarily: that costs a recruiter confirmation, never a wrong automatic pass.
* The six reported issues were not attached to the request, so the regression coverage is organised by the behaviors it listed: (1) all-of vs any-of, (2) examples treated as requirements, (3) alternatives accepted only after review, (4) merging and double counting, (5) language / soft skills kept separate, (6) ambiguity or missing evidence failing a gate or scoring zero. If a reported case is outside these, add it as a scenario in `tests/test_requirement_semantics_generalization_offline.py`.
