# Audit: the stored TC20 comparison run (v2-3 vs v2-4) and the prompt worked examples

Status: **run evidence verified; raw answers audited; prompts audited.** The run files are stored in
`audit/run_tc20_v2-3_v2-4/` and a test reproduces the stored scores from them. The example-removal experiment is a
**proposal only**. No paid call was made, and no prompt, production behaviour, label, scorer, frozen artifact or cap was changed.

## 0. Inputs

| Item | sha256 (full) | Source |
|---|---|---|
| `calls.jsonl` (10 rows) | `705c5b37639549b5363cf27b12125180e967c3f432508e9985900743cd2e605e` | upload `16d3343d-calls.jsonl` |
| `scored.json` | `2eb263593f71fd5a55c1c5b017f148607126a7cc9944e9392618462a878893df` | upload `27b69479-scored.json` |
| `manifest.json` | `2598c35dcc3c2a3ef1818079c0a65980f9e4bda9c92ec537fb40cc8133a14f41` | upload `a08d7acf-manifest.json` |
| stored TC20 evidence | `86a9abe7991ac55899296eb37e2133c5a51280c6d0067b106269bd8777b56590` | `evidence.json` (unchanged) |

The four earlier uploads (12-case benchmark files) are not part of this run and are not used here.

## 1. Provenance and reproduction: VERIFIED

Checked by `tests/test_requirements_v2_tc20_run_audit.py` (4 tests, passing):

- The manifest matches the frozen inputs: labels sha `6a87da80…`, JD sha `c0c7132c…`, prompt shas `21a2f942…` (v2-3) and
  `dd2651bc…` (v2-4), context sha `122ef7e4…`, user-message sha `ff6b334a…`, plan total `0.058595` (cap `0.10`).
- The two arms sent the same user message (the executor refuses otherwise; the manifest records one hash).
- Every saved answer was re-scored with the unchanged `score_answer` and `report`. The reproduced report equals the stored
  `scored.json` report exactly, for both arms, on every field.
- The recorded cost adds up: the ten per-call costs sum to `0.017366`, the stored total. Every call ended with `finish_reason:
  stop`, model `gpt-4o-mini-2024-07-18`, no error. The v2-3 prompt-token count (6485) matches the stored production call.
- No answer contains an item whose source text is not verbatim in the JD.

## 2. The raw answers: confirmed findings

Counts are over the 10 answers (5 per arm) unless stated. "Confirmed" means read directly in the saved answers.

| # | Finding | v2-3 | v2-4 | Rule / passage |
|---|---|---|---|---|
| R1 | Shape is identical in every call: 16 items (skills 4, experience 4, education 1, soft_skills 7); no warnings | 5/5 | 5/5 | rule 4 (warnings clause, line 58) not used |
| R2 | No duty item (`origin` = `from_responsibilities`) | 0/5 | 0/5 | rule 7 (line 81) |
| R3 | "Ability to communicate effectively with users …" extracted, as a soft_skills item | 5/5 | 5/5 | rule 5 soft_skills (line 74) |
| R4 | "Good understanding of business applications, digital platforms, and software support processes." extracted | 0/5 | 0/5 | rule 1 |
| R5 | "Ability to support system testing, troubleshooting, issue tracking, and operational follow-up activities." extracted | 0/5 | 0/5 | rule 1, rule 4 (comma list) |
| R6 | "Strong communication, coordination, and teamwork skills" kept as ONE soft_skills item | 5/5 | 5/5 | rule 4 AND (line 56) |
| R7 | Experience "1–3 years … or software implementation projects": subject is the full list, `alternatives` absent | 5/5 | 5/5 | rules 4 and 6 (lines 57, 79) |
| R8 | "Familiarity with business process documentation … or enterprise applications": in **experience**, not skills; `alternatives` absent | 5/5 | 5/5 | rule 4; rule 5 skills (line 70) |
| R9 | "Knowledge of the local business and regulatory environment": in **experience**; domain_knowledge is empty in all 10 | 5/5 | 5/5 | rule 5 domain_knowledge (line 75) |
| R10 | Education "… or a related field": `alternatives` present | 5/5 | 3/5 | rule 4 (line 57). **v2-4 dropped it in calls 1 and 2.** Not in the labels, so not in `scored.json` |
| R11 | Location: "Ramallah" routed to non_scoreable `location` | 3/5 (calls 1 and 4 absent) | 5/5 | rule 9 (line 85) |
| R12 | Reporting, "submit deliverables … for review and approval": routed to non_scoreable, label `other` (v2-3) or `reporting_line` (v2-4) | 3/5 `other`; 2/5 absent | 5/5 `reporting_line` | rule 9 (line 88 table: reporting_line is informational) |
| R13 | Reporting, "maintain regular communication with …": routed to non_scoreable `reporting_line` | 0/5 (absent 5/5) | 3/5 (absent 2/5) | rule 9 |
| R14 | `informational_items` and `post_hiring_conditions` are empty in every answer | 5/5 empty | 5/5 empty | rule 9 |
| R15 | `document_submission` is never used | 0/5 | 0/5 | rule 9 (line 87) |
| R16 | JavaScript kept as a preferred skill with only the word "JavaScript" as text (the broader "web technologies and scripting languages" is not in the text) | 5/5 | 5/5 | rule 1 (wording only; unscored) |
| R17 | Arabic and English split into two items | 5/5 correct | 5/5 correct | rule 4 |
| R18 | Distinct answers over the calls: v2-3 has 4 distinct answers in 5 calls (calls 1 and 4 are identical); v2-4 has 5 | | | consistency |
| R19 | No answer copies wording from the worked examples (searched for their distinctive terms) | none | none | section 3 |

## 3. The partial-coverage claims: verdicts

**(a) "User communication is extracted; two other competency lines are missing." CONFIRMED** (R3, R4, R5, in both arms and
all 10 answers). The mechanism is open. The worked examples contain no soft-skill item (census), which fits the hypothesis that
the communication sentence is kept because it matches soft-skill vocabulary, while the other two lines are not. That is
consistent with the prompt but not shown by these answers.

**(b) "Reporting appears but is routed to the wrong list." CONFIRMED, with a correction to my earlier hypothesis.** The reporting
sentences went to non_scoreable_requirements, not to informational_items (R12, R13, R14). My earlier hypothesis H3 said the
verb "submit" pulled the sentence into `document_submission` (post-hiring). **That is rejected**: `document_submission` was never
used (R15). The raw answers show a different error: the model placed the reporting statement in the location list, using the
label `other` (v2-3) or `reporting_line` (v2-4). In v2-4 the label is right and the list is wrong, which is the exact mismatch
rule 9 forbids ("exactly ONE of three lists"; the label "is never a list name" and does not choose the list).

**(c) "Duties are absent in all 10 answers." CONFIRMED** (R1, R2). Hypothesis H1 (the length of the examples sets the expected
output size) remains unproven: no answer copies example wording (R19), so any example effect would have to be at the level of
shape, not copying.

## 4. Prompt audit: additions to AUDIT of the prompts

Prompt text unchanged from the previous audit. What the raw answers add:

- **Confirmed: the v2-3 reporting rule is not followed.** Line 88 places `reporting_line` in informational_items. The answers
  never do this (R12–R14). The rule is clear; the model's choice is not what the rule says.
- **Confirmed: the v2-4 rule-9 sentence names no list.** v2-4 line 88 reads: "Statements about where the work is done (offices,
  sites, cities, regions), and statements about who the candidate reports or submits work to, … are conditions even when they sit
  in a responsibilities section." It says "conditions", not "informational_items". The v2-4 answers used the new label
  `reporting_line` but still put the statements in non_scoreable (R12, R13). The sentence did not steer the list. This is a defect
  in the candidate's wording, to be fixed only after the proposed experiment decides what to change.
- **Confirmed: the `other` label is used against its definition.** Rule 9 says "other" only when no label fits (v2-3 line 89).
  In v2-3 the reporting statement was labelled `other` in 3 of 5 calls, although `reporting_line` fits.
- **Confirmed: the v2-4 candidate's education regression is not described by any rule change I made.** Rule 4 (line 57, unchanged
  in v2-4) still requires alternatives for an "or" list, and v2-4 drops them in 2 of 5 calls (R10). The cause is open. Hypothesis
  H5: the COVERAGE pass added to v2-4 (its line 47) draws attention to item counts and away from alternatives. Not tested.
- **Confirmed: contradictions between examples and rules: none** (unchanged from the earlier audit).

## 5. Proposal only: example-removal experiment

Build nothing and run nothing until approved.

- Variant: v2-3 with the EXAMPLES block (lines 99–182) removed; rules and output format byte-identical to v2-3.
- Input, model, settings and budget: as the TC20 run (5 calls, about $0.03 worst case, cap $0.10, no retries).
- Scoring: the same labels and eight checks, plus the unlabelled observations R2–R15 read from the answers. Labels and scorer unchanged.

**What it can show:** whether removing the examples changes the pass counts on this JD, in a direction. One JD, five calls, and no
copying evidence (R19) limit how far a result can be trusted.

**What it cannot show:** that the examples are the sole cause, or that they are a cause at all. Removing examples also removes
the only worked soft-skill-free and reporting-free patterns the rules depend on; a change in output could come from that, not from
the omissions themselves. A null result would not exonerate the rules either.

**Decision it informs:** if the duties, reporting and competency items come back with the examples removed, the examples are not
the lever and the next step is a rule-salience test; if they change, the examples are one lever, to be tested with examples
rewritten rather than removed.

## 6. Open items

1. Approve or decline the example-removal control (section 5). Nothing has been built for it.
2. The education-alternatives regression in v2-4 (R10) and the v2-4 rule-9 wording (section 4) are open. Fixing either is a
   prompt change and needs a decision after the control.
3. The labels do not score the education alternatives or the routing of reporting statements to informational. Adding labelled
   checks would change the labels and needs a separate decision.
