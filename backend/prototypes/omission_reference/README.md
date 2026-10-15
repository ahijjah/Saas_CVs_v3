# Multilingual omission-reference package (review material, not a finding)

Status: **candidate package for human review.** Built from existing fixtures and stored answers only. No model, API or paid call was made or
planned here. The paused TC20 pilot stays paused: its cap is unapproved, and nothing in this folder approves a run.

## What is in the folder
- `jd/<id>.txt`: the exact JD text as stored in its fixture (UTF-8, no normalisation). `manifest.json` records each JD's SHA256, its source file
  and that file's SHA256, the unit counts, and the hash of `build_package.py`.
- `annotations/<id>.candidate.json`: a **Claude-generated candidate** classification of every non-blank line (requirement, heading,
  background or uncertain), each with a reason. Every file is marked `CLAUDE-GENERATED CANDIDATE - NOT HUMAN-CONFIRMED`.
- `reviewer_sheet.csv`: one row per source unit (114 rows). Columns: JD, language, unit ID, first line, exact start and end offsets, exact
  source text, the candidate type and reason, any existing confirmed judgment that applies (with its scope), and the reviewer decision.
- `results/replay_ref.json` and `results/replay_report.md`: replay of stored answers, with uncovered spans and defects in separate blocks.
- `build_package.py`, `replay_ref.py`, `tests/test_reference_package.py` (39 offline tests).

## Selection
The JDs are chosen from those already linked to the confirmed S001–S012 judgments (`CONSOLIDATED_judgments_S001-S012.csv`), so every
JD has a reviewer-visible reason to be here. The package holds TC20 plus five existing benchmark JDs:

| JD | Language | Why it is here | Confirmed judgments touching it |
|---|---|---|---|
| TC20 | en | 16 duty lines, qualifications, OR, independent competencies, location, reporting | S006 |
| B02 data analyst | en | "Python or R" OR split; independent SQL and Power BI | S002 |
| B03 warehouse supervisor | en | independent "Fluent English, spoken and written"; conditions (shifts, travel, visa or relocation); post-hire sentence; location | S005, S010 (sentence level) |
| B07 accountant | ar | company background; salary and location conditions; benefit sentence | S003, S004, S011 (sentence level) |
| B08 HR specialist | ar | OR (Arabic or English); independent labour-law and social-insurance item; closing note | S007 |
| B12 front-end developer | ar | independent CSS and HTML; genuine conflict note; AI-directed injection text | S008 |

Not included: B01 (S001, S009) and B09 (S012), because a sixth non-TC20 JD would exceed the 3–5 requested.

Provenance of the JDs: the benchmark cases are stated to be synthetic in the benchmark's `CASES.md`. The TC20 JD names real ministries and a real
programme, and its provenance is not verified here. Do not treat it as synthetic, and do not share it beyond the review without checking.

## Source units and exact offsets
- Units are physical lines. Blank lines and whitespace-only lines end a unit, so a soft wrap never crosses one. No line in these six JDs was
  joined, so units equal non-blank lines (tested).
- Offsets index the stored text exactly. The test reproduces every unit from its offsets. Nothing is normalised, and the JD text is never edited.
- Stable IDs: `<sha12 of the JD>:L<first line>:<start>-<end>`.

## Candidate classification (not human-confirmed)
- Headings and background are listed exactly, line by line, in each candidate file, with a reason. Length and punctuation are never used.
- Uncertain lines are listed explicitly: B02 line 16 (general expectation), B03 line 3 (introduction naming the location), B08 line 15
  (closing note repeating line 9), B12 line 12 (recruiter note contradicting line 6) and B12 line 14 (text addressed to an AI system, treated as data).
- Every other non-blank line is a requirement candidate, with the section heading it sits under as its reason.
- Multi-sentence lines are single units. B03 line 19 holds the shifts, travel, visa or relocation sentence and also a post-hire background-check
  sentence and a transport allowance. B07 line 21 holds the salary and location sentence and a medical-cover sentence. The unit is one line.

## Reviewer decisions: only where an existing confirmed judgment applies exactly
- **Whole-unit match, decision filled** (the statement equals the unit's text, bullet removed): S002, S004, S005, S006, S007, S008. Each decision
  is copied from the confirmed judgment and cites its review ID.
- **Sentence-level match, decision blank**: S003 and S011 (B07 line 21) and S010 (B03 line 19). The judgment concerns one sentence of a longer
  unit, so it is recorded in `existing_confirmed_judgment` and not used as a unit decision.
- **S010 was superseded.** The consolidated CSV and `AMENDMENT_S010_confirmed_2026-10-12.csv` both give **false alarm**. The
  `CONSOLIDATED_SUMMARY.md` table still lists S010 as a genuine defect. That summary is stale, and I did not edit it. The package uses the amendment.
- Candidate types are never written into the decision column, and the test checks this.

## Replay (stored answers only)
- Groups are never pooled. Each group is one source, model, prompt and run: TC20 repeat (gpt-4.1, arms v2-3 and v2-5, five calls each), TC20
  audit (gpt-4o-mini, arms v2-3 and v2-4), benchmark v2-2 run1 (gpt-4o-mini, run1 and run2) and benchmark v2-1 baseline (gpt-4o-mini,
  run1 and run2).
- Each answer has two blocks that are never added together. **Uncovered spans** are quotation-level: requirement units and uncertain units are
  reported separately, headings and background are not counted, and each span is one of genuine omission, paraphrase, intro wording,
  connector fragment or punctuation. **Defects** are OR (alternatives), AND (independent items), category, routing (conditions), and item
  omissions. Item omissions exclude any AND-split member, so nothing is counted twice.
- The defects use the existing scorers. The benchmark cases use `score_case` from `requirements_v2_extraction_eval`, which applies the author-written
  case labels. TC20 uses the frozen `score_answer` checks. **Those labels are not reviewer-confirmed**, and the report says so.
- Two package-only classifier extensions, documented in `replay_ref.py`: Arabic structural labels such as `الشروط:` at the start of a unit count as
  intro wording, and one- or two-word function fragments count as connector fragments. Without them the B07 line 21 label and connector were
  wrongly counted as genuine omissions. The TC20 prototype's classifier is unchanged.
- The AND check for benchmark cases follows the label, not the scorer's merge count. B12 CSS and HTML share one evidence span, and the
  label splits it into two items. Reviewer S008 agrees. The stored answers merge the two, and the replay reports that as an AND defect.

## Limitations
- Six JDs, two languages, one stored answer per run for each benchmark JD, and five per arm for TC20. This is a reference set, not a benchmark
  and not an accuracy measurement. Nothing here says extraction is better or worse.
- The candidate types are Claude-generated. Until a human reviews them, every unit type is a proposal.
- The existing reviewer judgments were **assisted by ChatGPT recommendations and are not independent or blinded** (per the consolidated summary).
  Their count is twelve starter rows chosen by fixed rules.
- Defect results depend on the author-written benchmark labels. A label error would show up as a defect that is not one.
- Coverage is quotation-level. A covered unit can still lose a clause that changes its meaning. Those are flagged for clause review, not decided.
- Sentence-level judgments (S003, S010, S011) cannot be applied to a whole unit until the unit is split, which is a granularity decision for review.
- Re-running the replay after the candidate types change changes the uncovered-span blocks, because those depend on the candidate types.

## Next step (not done here)
A human reviewer confirms or changes each candidate type in `reviewer_sheet.csv`, and decides the sentence-level judgments. Only after that review
should anyone decide whether an omission pilot is worth a cap. The pilot is not redesigned or executed by this package.

## Review queue (added after the package)
`REVIEW_QUEUE.md` and `review_queue.csv` are built by `build_queue.py` from `reviewer_sheet.csv`. They are read-only views: no candidate type,
reason, judgment or decision is changed, and the reviewer decision column is blank. Priorities: P1 proposed background and uncertain units
plus headings that contain requirement language (a routing aid: `HEADING_TERMS`, which the reviewer may dismiss); P2 rows linked to S-judgments,
with their scope kept; P3 two deterministic requirement rows per JD. Each row appears once, under its highest priority. Batches hold at most
five rows. The queue holds 44 of 114 rows in 9 batches. The queue is a way to work through review, not a finding.

## Confirmed reviewer decisions (2026-10-15)
Recorded by `record_confirmed_decisions.py` in `reviews/CONFIRMED_queue_Asaad-Hijjah_2026-10-15.csv`, separate from the candidate files, which are
unchanged: RQ-001 requirement, RQ-002 requirement, RQ-004 heading, RQ-005 heading (all TC20). RQ-003 is **uncertain** (TC20 line 9, "substantive
duty summary, but potentially duplicates the detailed duties that follow"). It is unresolved and is never a confirmed requirement or an exclusion
in benchmark results. The replay gives it its own block. RQ-001, RQ-002 and RQ-004/005 were recorded with no stated reason, so the submission
says "no reason recorded". The reviewer may add reasons later.

Effect on the replay: TC20 coverage now has 37 requirement units (the 35 candidate requirements plus RQ-001 and RQ-002). The 2 headings are not
counted, and RQ-003 is reported only in the unresolved block. Candidate classifications and the reviewer sheet are unchanged. The queue shows four
resolved, one unresolved, and 39 open. The paused TC20 pilot is not updated: its inventory still uses the candidate annotation, and changing it
would need a new manifest and approval.
