# Requirements v2: decision report on the prompt experiments

Scope: every stored experiment on the TC20 job description (five batches, 60 calls, all on `gpt-4.1-2025-04-14`). Each batch is read from
its own stored files and reported separately. No batch is pooled into another, and no original score has been changed. Every test here is
within one batch (Fisher's exact test, two-sided). Nothing here is a general reliability claim. No new candidate, paid call, activation,
deployment or production change is part of this report.

## 1. Run ledger (per batch; counts of calls)

| Batch (commit) | Arm (prompt bytes) | Calls | Reporting, all 3 | Location correct | Duties 16/16 | C_EXP_OR | C_FAM_OR | C_AND |
|---|---|---|---|---|---|---|---|---|
| Paired 1e73231 | v2-3 (control, 21a2f942) | 5 | 4 | 4 | 4 | 0 | 0 | 0 |
| Paired 1e73231 | v2-5 (f1569a8b) | 5 | 1 | 5 | 5 | 0 | 0 | 0 |
| Repeat 23cce8e | v2-3 | 5 | 5 | 5 | 4 | 4 | 2 | 0 |
| Repeat 23cce8e | v2-5 | 5 | 0 | 5 | 5 | 0 | 0 | 0 |
| Example 5 4ea4323 | FULL (v2-5) | 5 | 4 | 5 | 5 | 1 | 1 | 0 |
| Example 5 4ea4323 | VARIANT (v2-5 without Example 5) | 5 | 0 | 5 | 5 | 1 | 0 | 0 |
| Scope 22efcc0 | FULL (v2-5) | 5 | 1 | 5 | 5 | 0 | 0 | 0 |
| Scope 22efcc0 | CLARIFIED (COVERAGE sentence, 266329fc) | 5 | 5 | 3 | 5 | 1 | 1 | 0 |
| Routing c55025d | CONTROL (= CLARIFIED bytes) | 10 | 6 | 10 | 10 | 0 | 0 | 0 |
| Routing c55025d | ROUTED (CLARIFIED + location sentence, f5042e34) | 10 | 3 | 10 | 10 | 0 | 0 | 0 |

"Reporting, all 3" is the whole-answer check of the three reporting sentences. Across the 60 calls there is one partial answer (paired
v2-3 call 1, with only "maintain regular communication"); every other answer has all three or none. Duties 16/16 counts calls with all
sixteen responsibility lines. The OR and AND columns are the frozen checks.

## 2. Changes: demonstrated benefit, unresolved, or regression

| Change | Evidence in its own batch | Status |
|---|---|---|
| **Duty completeness, v2-5 family vs v2-3** | v2-3 lost all 16 responsibility lines in one call per batch (2 of 10); v2-5 family: 0 such calls in 50 | **Consistent direction, not established per batch.** Each batch alone is 1 of 5 against 0 of 5 (p = 1.0). |
| **Reporting, v2-3 vs v2-5** | Paired: 4 vs 1 (p = 0.21). Repeat: 5 vs 0 (p = 0.008) | **Possible regression of v2-5.** Both same-batch comparisons point the same way; one reaches nominal significance and neither survives a multiplicity correction. |
| **Example 5 (removal)** | FULL 4/5 vs VARIANT 0/5 on reporting (p = 0.048); OR/AND unchanged | **Removal did not help.** The hypothesis that Example 5 causes the omission is not supported. Keeping Example 5 is the better-supported option, but the effect is not resolved. |
| **COVERAGE clarification (CLARIFIED)** | Scope batch: reporting 5/5 vs FULL 1/5 (p = 0.048); location 3/5 vs 5/5 (p = 0.44) | **Unresolved.** The one favourable batch is not reproduced: the same prompt bytes gave reporting 6/10 and location 10/10 in the routing batch. |
| **Location routing sentence (ROUTED)** | Location 10/10 vs 10/10 (no room). Reporting 3/10 vs 6/10 (p = 0.37) | **No benefit demonstrated. Guard regression in this batch, not attributed.** The protocol reports it as a regression and does not accept the sentence. |

Summary: no change has a demonstrated, reproduced benefit over the v2-3 control. The duty result is the most consistent signal, but it has not
been tested within a batch. The reporting signal, if real, works against v2-5.

## 3. Defects that persist across batches

Counts are for all calls in all batches (60 calls, 10 in the paired and repeat batches for v2-3 and v2-5 together).

- **Communication line never split:** `C_AND` fails in 60 of 60 calls, in every arm of every batch. The rule that requires the split is
  rule 5 of the prompt. This is an instruction-following defect that no prompt change has moved.
- **ICT and familiarity OR lists not kept:** `C_EXP_OR` passes in 4 of 10 v2-3 calls (repeat only) and in 3 of 50 v2-5 family calls.
  `C_FAM_OR` passes in 2 of 10 v2-3 calls and 2 of 50 v2-5 family calls.
- **Reporting merged into one item:** in 15 of the 20 v2-5 family answers that contain the reporting sentences, two or three sentences sit
  in one `reporting_line` item. The same merge appears in 4 of 9 such v2-3 answers. The protocol counts this as routed; it is a source defect.
- **Reporting omitted:** 30 of 50 v2-5 family calls lack the reporting sentences. 1 of 10 v2-3 calls lacks them, and that call has only
  one of the three (see section 4).
- **Responsibilities omitted (all 16 lines):** v2-3, 2 of 10 calls. v2-5 family, 0 of 50. This is a control defect, not a v2-5 defect.
- **Experience field:** one preferred experience item has `experience: null` in 9 answers and an object in 1 (scope CLARIFIED call 5).
  Minor, unscored.
- **Category weights:** vary across answers in both arms (education 10 to 20, domain 0 to 10). Not scored.

Schema conformance and invented sources were clean in all 60 calls, under the structural checks defined for these experiments.

## 4. Wording, scoring, and model variability

**Prompt wording (plausible contributors, not established):**
- Rule 9 gives labels a table but does not bind them to lists. A location statement was misrouted in 2 of 5 CLARIFIED answers, although
  the table already puts location in `non_scoreable_requirements`.
- Rule 10 keeps restated statements separate. It does not cover one source span that holds two statements, which is how the reporting merge
  happens.
- Rules 4 and 5 state the OR and AND requirements explicitly. The model does not follow them in 60 of 60 calls on AND. Whether the wording
  is unclear or the model cannot follow it at this output length is not known.

**Scoring limitations (frozen checks; not changed):**
- `C_REPORTING` checks the single sentence "submit deliverables" only. It passes with that sentence alone and fails with the other two.
  In the paired batch, v2-3 call 1 has only "maintain regular communication": the frozen check is false, correctly by the sentence it looks
  for, and the whole-answer check also counts it as incomplete. The frozen check is blind to the other two sentences.
- `C_EXP_OR` requires the alternatives list to equal the four labelled ICT items exactly. A correct list with a different wording fails.
- `C_AND` requires separate communication, coordination and teamwork items. It matches rule 5, so the failures are model failures, but the
  check depends on how the items are phrased.
- `C_LOCATION` checks for the location marker in `non_scoreable_requirements` only. It does not check for a duplicate in another list.

**Model variability (large, and visible with no prompt change):**
- FULL v2-5 reporting across four batches: 1 of 5, 0 of 5, 4 of 5, 1 of 5.
- The same CLARIFIED bytes: location 3 of 5 in one batch, 10 of 10 in another.
- v2-3 OR (`C_EXP_OR`): 0 of 5 in one batch and 4 of 5 in another.

Batch-to-batch shifts of 3 to 4 calls out of 5 occur without any prompt change. Every single-sentence effect measured so far (2 to 4 calls out
of 5 or 10) lies inside this spread. Most of the differences reported above are therefore within model variability, and the batches are too
small to separate a real effect from it.

## 5. Are continued small prompt additions justified?

No, not as the main route.
- Each addition has been judged on one batch of 5 to 10 calls. At this variance none of them can be resolved.
- The prompt has grown from 25 KB (v2-3) to 30.7 KB (routed), and each addition increases the chance that rules conflict.
- The defects that matter most for scoring, the AND split and the OR lists, have not moved in 60 calls, despite explicit rules. Adding a rule
  has not changed what the model does with them.

A different approach is worth considering, but each option needs its own design and approval:
- **Two-pass decomposition:** first list every line of the JD verbatim, then classify each line. This targets omissions and merges.
- **Deterministic post-processing in the parser:** split the communication, coordination and teamwork list, and route by label. This makes
  AND and location routing independent of model compliance. It is a parser change, outside this scope.
- **Structured output** enforces the schema but not the routing or the splits, so it does not address these defects.

## 6. Recommendation: one next step

**Run one pre-registered replicate of the existing pair, v2-3 against v2-5 FULL, with no new wording.** Twenty calls per arm, the TC20 JD,
the same user message, model and settings.

Rationale. The decision that matters now is whether v2-5 should replace v2-3 as the offline candidate. The evidence points in opposite
directions: duties favour v2-5, reporting favours v2-3, and both comparisons are within-batch and small. Only a larger same-batch comparison
can settle it. It uses bytes that already exist, so it adds no new prompt, and it costs roughly $1.5 expected (worst case $3.2036, so a cap of
at least $3.25).

Pre-registered primary outcome: reporting complete (all three sentences, whole answer). Secondary: duties 16 of 16.

Stopping rule, fixed before the run. Use a two-sided Fisher test at 20 calls per arm. A gap of about 9 calls is needed for p < 0.05, for example
13 of 20 against 4 of 20 (p = 0.0095). A gap of 6 (12 against 6) gives p = 0.11, which is not decisive.
- **If v2-3 exceeds v2-5 by a decisive margin on reporting:** stop v2-5 work; keep v2-3 as the control.
- **If the reporting difference is not decisive and v2-5 is not fewer duty-complete calls than v2-3:** v2-5 is not shown to be worse on
  reporting. Decide on duties and the other evidence, and stop prompt experiments.
- **Otherwise:** the result is inconclusive. Stop prompt experiments and decide on a structural approach (section 5), not another sentence.

In every outcome: no further rule sentences are added before the decision above, and nothing is activated by this replicate.
