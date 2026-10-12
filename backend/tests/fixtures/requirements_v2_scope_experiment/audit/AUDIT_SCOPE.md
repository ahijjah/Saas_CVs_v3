# Audit of the scope-clarification experiment (commit 22efcc0)

Status: **provenance verified; costs verified; scored.json reproduced exactly by the unchanged scorer; all ten raw answers inspected.**
Nothing was run, activated or changed: no paid call, no prompt, label, scorer, frozen artifact or production setting. Both prompt arms stay
inactive. Historical runs are reported separately and are not pooled with the fresh arms.

## 1. Files and provenance (stored byte for byte in `../run/`)

| File | sha256 |
|---|---|
| `calls.jsonl` (10 rows) | `6cda6885348189bcb8c8afceaa94a763e96cac014b18061870443dc5e7e376a3` |
| `manifest.json` | `c688df6e3f6596cb589dd6328279b07e5182af8b3968cab1f909dc9f686fee9f` |
| `run.json` | `980a117036e0da257a2b9ea031a9e77ff9dd31f50e1f5f2bc227babd3c0af090` |
| `scored.json` | `8017082df622a891c60508e8b5c82a02033b6808667ce295e57ddf92859131ac` |

- Manifest: git commit `22efcc0132014f5efdccaf24cedd155bbb0502cc` (it exists in the repository); protocol `5210b3ed…91482` (the file in
  `../PROTOCOL.md` hashes to this value); scope script `b95290af…14e09ad`; FULL = `f1569a8b…` (criteria_extraction_v2-5, unchanged);
  CLARIFIED = `266329fc…d61103e` (30,550 bytes; FULL is 30,430 bytes; one sentence replaced, `64f3f06c…` → `1ef27d14…`);
  user message `ff6b334a…240473`; labels `6a87da80…`; frozen TC20 scorer `1c40def4…`.
- Every pin equals the current file in this repository.
- Ten rows, alternating, FULL first. Every row: its arm's system hash, the user hash, model `gpt-4.1-2025-04-14` (requested and returned),
  finish `stop`, no error.

## 2. Costs

- Per-call costs equal `prompt_tokens × 2e-6 + completion_tokens × 8e-6`, rounded to six places.
- Spend **0.354424 USD** (the owner's figure) = `run.json` = `scored.json`. FULL 0.172542; CLARIFIED 0.181882.
- Reservation 0.82772 USD under the 1.00 cap. Spend is 42.8 % of the reservation.

## 3. Reproduction

`requirements_v2_scope_experiment.report(rows, labels, jd)`, with the frozen TC20 labels and scorer, returns the saved `scored.json` report
**in full** (per-call records, fresh arms, direct comparison, historical context). `run.json` equals `scored.json["run"]`.

## 4. Reporting: retention and routing (whole raw answer)

| Order | Arm, call | All 3 sentences in whole answer | Routed as `reporting_line` | Reporting items (sentences per item) | Primary outcome |
|---|---|---|---|---|---|
| 1 | FULL, 1 | no | — | 0 | fail |
| 2 | CLARIFIED, 1 | yes | yes | 2 (1, 3) | pass |
| 3 | FULL, 2 | no | — | 0 | fail |
| 4 | CLARIFIED, 2 | yes | yes | 2 (1, 3) | pass |
| 5 | FULL, 3 | yes | yes | 2 (1, 3) | pass |
| 6 | CLARIFIED, 3 | yes | yes | 2 (1, 3) | pass |
| 7 | FULL, 4 | no | — | 0 | fail |
| 8 | CLARIFIED, 4 | yes | yes | 2 (1, 3) | pass |
| 9 | FULL, 5 | no | — | 0 | fail |
| 10 | CLARIFIED, 5 | yes | yes | 2 (1, 3) | pass |

- Where present, the three sentences are never partial: all three or none. The whole-answer check agrees with the scored flags in all ten calls.
- **Merge defect (shared by both arms).** In the three-sentence answers, the second reporting item holds three sentences: "in coordination
  with the MoNE IT focal point …" and "maintain regular communication …" (with the consultation sentence) in one item. Rule 9 puts each
  statement in one list, and rule 10 keeps restated statements separate. The same merge appears in FULL call 3 (order 5), so it is not a
  variant effect. The first reporting item holds the single submit-deliverables sentence.

## 5. Location: the regression (CLARIFIED calls 2 and 5)

| Order, arm, call | `non_scoreable_requirements` has location | `informational_items` has location |
|---|---|---|
| 1 FULL 1 | yes | no |
| 2 CLARIFIED 1 | yes | no |
| 3 FULL 2 | yes | no |
| **4 CLARIFIED 2** | **no (empty)** | **yes** (`category: location`) |
| 5 FULL 3 | yes | no |
| 6 CLARIFIED 3 | yes | no |
| 7 FULL 4 | yes | no |
| 8 CLARIFIED 4 | yes | no |
| 9 FULL 5 | yes | no |
| **10 CLARIFIED 5** | **no (empty)** | **yes** (`category: location`) |

- The misrouted statement is the JD sentence "It is expected that the Technical Coordinator. will work at the MoNE office in Ramallah and as
  needed, at other MoNE offices in the West Bank…". Its source text is copied exactly (verified).
- **Self-contradiction in the misrouted answers.** Each misrouted item carries the reason "work location is not a scoreable requirement",
  so the model labelled it correctly and still put it in the informational list. The failure is in the **list choice**, not in the label.
- Rule 9's table already maps `location` to `non_scoreable_requirements` and lists no location item under informational. The table was
  present in all ten answers.
- **Possible mechanism (not tested).** In the JD this sentence sits directly under the heading "Reporting and Supervision", with the
  reporting sentences in the same section. The two misrouted answers placed it in the same list as the reporting lines. The clarified
  sentence also adds "informational statements" as a destination, which widens the informational list. Either could explain it. Five calls
  per arm cannot separate them.
- Frozen check `C_LOCATION`: FULL 5/5; CLARIFIED 3/5 (calls 2 and 5 fail).

## 6. Duty coverage, OR alternatives, AND splitting

- **Duties:** 16 of 16 in all ten answers.
- **OR, ICT list** (`alternatives` kept): CLARIFIED call 5 only (1 of 5); FULL 0 of 5. `C_EXP_OR` FULL 0/5, CLARIFIED 1/5.
- **OR, familiarity list:** CLARIFIED call 5 only (1 of 5); FULL 0 of 5. `C_FAM_OR` FULL 0/5, CLARIFIED 1/5.
- **AND splitting:** the line "Strong communication, coordination, and teamwork skills" is one item, in `soft_skills`, with no alternatives, in
  all ten answers. `C_AND` fails 10 of 10. This is a separate defect and is not addressed by the scope clarification.

## 7. Other categories, importance, schema and source

- **Importance and cues:** every item is `preferred` exactly when its `importance_cue` is non-null, and every cue occurs in the JD.
- **Source text:** 0 invented source texts in all answers (each occurs in the JD after whitespace is collapsed). No item in a category outside
  `experience` has `origin: from_responsibilities`.
- **Schema:** all ten answers conform to the output contract (top-level keys, seven category keys, item and condition keys, enumerated
  values).
- **Warnings:** empty in all ten answers.
- **Experience field (one item):** "Experience in supporting, testing, implementing, or operating business systems …" is a preferred experience
  item. Its `experience` is `null` in nine answers and an object (subject set, `min_years` null) in CLARIFIED call 5 (order 10). The contract
  allows the object with null fields. This is a minor schema inconsistency in the same item across calls. It is not scored.
- **Category weights (not scored):** education 10 to 20, domain_knowledge 0 to 10, experience 35 to 40, varying across answers in both arms.
  Descriptive only.

## 8. Historical runs (context only, not pooled)

| Run (stored file) | FULL: primary outcome (all 3 retained and routed) |
|---|---|
| Paired run, commit 1e73231 | 1 of 5 |
| Repeat, commit 23cce8e | 0 of 5 |
| Example 5 experiment, commit 4ea4323 (its FULL arm) | 4 of 5 |
| **This experiment, commit 22efcc0 (FULL)** | **1 of 5** |

The FULL prompt's primary outcome has been 0, 1, 4 and 1 of 5 across four runs (6 of 20 in total). That spread is wide.

## 9. Interpretation (what the data support)

- **Reporting improvement.** Primary outcome CLARIFIED 5/5 versus FULL 1/5, difference +4. This meets the protocol's supporting threshold
  (D ≥ 3) for this JD and this run. It is not evidence of causation: the FULL baseline here (1/5) is low relative to the Example 5 run (4/5),
  and FULL's history spans 0 to 4 of 5. It is also not evidence of general reliability: one JD, five calls per arm, two prompts.
- **Location regression.** CLARIFIED 3/5 versus FULL 5/5 on `C_LOCATION`, a loss of two calls. The protocol keeps group losses separate
  from the primary gain. The gain does not offset the loss, and the loss does not cancel the gain.
- **OR and AND are not improved** by the clarification (one call each). They are separate defects.

## 10. Assessment: would explicit label-to-list routing address the location errors?

- The observed error is a **list-choice error**. The model knew the label ("not a scoreable requirement") and put the item in the
  informational list anyway. A rule that binds the category to the list directly targets that failure.
- Limits: rule 9 already states this mapping, and the misrouting still occurred in two of five clarified answers. Restating the table may
  reduce the error without removing it. Whether it does needs a test with more calls. Nothing here shows the mapping would work.
- A deterministic check (reason says "not scoreable" while the item sits in informational) would detect this error, but it belongs in the
  parser and is a production change. It is out of scope for this audit.

## 11. Recommendation (not built or run)

1. **Smallest next correction:** a single rule-9 sentence that binds the labels to their lists, for example "A statement whose category is
   location, work_authorization, salary, availability, travel or schedule always goes in non_scoreable_requirements, never in
   informational_items." Keep the COVERAGE clarification as in the variant. Do not change the OR and AND text in the same change.
2. Test it with a protocol written before the calls. Use more than five calls per arm, because the FULL baseline swings between 0 and 4.
   The primary outcome should stay the reporting sentences, with `C_LOCATION` reported as its own group.
3. **Separately:** the ICT and familiarity OR items and the communication AND split are not fixed by either change. They need their own
   instruction and test. The merged reporting item and the experience-field inconsistency are minor and can wait.
4. Both variants stay inactive. Nothing here decides whether any prompt is used.
