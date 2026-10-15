# Scope-clarification experiment: protocol and interpretation rules

Recorded BEFORE any call. Its sha256 is pinned in `scripts/requirements_v2_scope_experiment.py` and written into the run manifest.
Starting point: commit `361a62a` (the verified Example 5 audit). Nothing here activates a prompt: both arms stay inactive.

## Question

Does making the COVERAGE obligation explicit about the rule 9 lists (non-scoreable requirements, post-hiring conditions and
informational statements) change how the model carries the reporting statements of the TC20 job description into its answer?
Hypothesis H2: the unclear scope of the COVERAGE obligation contributes to the reporting omission on this JD.

## Arms (fresh calls only)

- **FULL**: `criteria_extraction_v2-5` as it is (sha256 `f1569a8b…f1569a8b`). Unchanged.
- **CLARIFIED**: `criteria_extraction_v2-5-coverage` (sha256 `266329fc…d61103e`). The only change is one sentence of the COVERAGE
  paragraph: the obligation "… becomes an item (rules 4 to 8) or a condition (rule 9)." (sha256 `64f3f06c…033aef`, 118 bytes) becomes
  "… becomes an item (rules 4 to 8) or, when rule 9 applies, a condition in one of its three lists (non-scoreable requirements,
  post-hiring conditions or informational statements)." (sha256 `1ef27d14…48ae8`, 238 bytes). Every other byte, including Example 5,
  rule 9 and the output contract, is identical to FULL. This is tested byte for byte.

## Design

- Ten calls: FULL call 1, CLARIFIED call 1, FULL call 2, CLARIFIED call 2, … (five per arm, alternating, FULL first).
- The same stored TC20 JD, the VPS job context ("Technical Coordinator 20") and the same user message (sha256 `ff6b334a…240473`).
- Model `gpt-4.1-2025-04-14` (the returned model must equal it). Temperature 0.1, max_tokens 6000, JSON-object mode, timeout 90 s.
- No retries. Stop at the first failed call or the first unexpected returned model. Hard cost cap USD 1.00, enforced before each call.
  The reservation is computed exactly from the worst case of each call and printed in the dry run.
- The TC20 labels and the frozen TC20 scorer are reused unchanged. The output directory must be new or empty.

## Measures (defined before the calls)

1. **Primary outcome, per call:** all three reporting sentences are in the whole raw answer ("submit deliverables", "in coordination
   with the MoNE IT focal point", "maintain regular communication"), AND each of the three is in `informational_items` with
   category `reporting_line`. The check is case-insensitive on the sentence and is applied to the whole answer. P(arm) is the number of
   calls that meet the primary outcome.
2. **Secondary, reported separately:** the labelled reporting sentence in the whole answer (the frozen check's marker); the number of
   `reporting_line` informational items.
3. **The eight frozen TC20 checks**, reported separately for each arm: C_RESP, C_COMP, C_EXP_OR, C_FAM_OR, C_LOCAL, C_LOCATION,
   C_REPORTING, C_AND.
4. **Duty coverage:** duty lines found, of 16, per call.
5. **OR alternatives:** whether the ICT list and the familiarity list keep their alternatives.
6. **AND splitting:** the communication, coordination and teamwork line split into separate items (three or more).
7. **Schema:** the answer parses as one JSON object with exactly the top-level keys of the output contract; the seven category keys;
   every item has exactly the item keys; every condition has exactly the condition keys; `importance` and `category` take listed values.
   The count of schema violations per call is reported.
8. **Invented content:** the number of items or conditions whose `source_text` does not occur in the job description after
   whitespace is collapsed (rule 2 requires verbatim copies). The count per call is reported.

## Interpretation rules (fixed before the calls)

1. Let D = P(CLARIFIED) − P(FULL), within this experiment's fresh calls only.
   - D ≥ 3: the result **supports** the clarification helping the model keep the reporting statements on this JD. It is **not** proof
     of causation and says nothing about general reliability: one JD, five calls per arm, run-to-run variance (the same FULL prompt gave
     1, 0 and 4 of 5 reporting results in three earlier runs) and possible interactions remain.
   - −2 ≤ D ≤ 2: **inconclusive**. A small or absent difference is treated as inconclusive.
   - D ≤ −3: the clarification did **not** help on this JD in this run. It does not show that the clarification harms general reliability.
2. Compare the two fresh arms directly. Historical runs (the paired run, the repeat and the Example 5 experiment's FULL calls) are
   reported in their own section, labelled as history, and are never pooled with the fresh calls.
3. The eight frozen checks, duty coverage, OR alternatives, AND splitting, schema and invented content are reported as separate groups
   with their own counts. A gain in the primary outcome does not offset a loss in any group, and a gain in one group is not a gain in
   another. Differences in these groups are descriptive only.
4. Five calls per arm cannot separate a moderate effect from run-to-run variance. No threshold here is a significance test.
5. Nothing is promoted. v2-5 and the clarified variant stay inactive regardless of the result. A result here does not decide whether
   any prompt is used.

## Reproducibility

Every run manifest records: the sha256 of each arm's prompt, the byte identity of CLARIFIED to FULL (with the two sentences' hashes),
the system and user hashes of each call, the labels, the frozen scorer, the protocol, the experiment script and the git commit.
The dry run prints the reservation without any network call.
