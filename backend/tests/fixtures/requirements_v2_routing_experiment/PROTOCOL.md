# Location-routing experiment: protocol and interpretation rules

Recorded BEFORE any call. Its sha256 is pinned in `scripts/requirements_v2_routing_experiment.py` and written into the run manifest.
Starting point: commit `8c66334` (the audit of the scope-clarification experiment, commit `22efcc0`). Exploratory. Nothing here activates
a prompt: both arms stay inactive regardless of the result.

## Question

Does one explicit rule 9 sentence, binding the `location` label to `non_scoreable_requirements` wherever the statement sits in the job
description, reduce the location misrouting observed in CLARIFIED calls 2 and 5 of the scope experiment?

## Arms (fresh calls only)

- **CONTROL**: `criteria_extraction_v2-5-coverage` (the scope-clarification prompt, sha256 `266329fc…d61103e`). Unchanged.
- **ROUTED**: `criteria_extraction_v2-5-routing` (sha256 `f5042e34…56d5`). CONTROL plus exactly one sentence, inserted as the last line of
  rule 9 (before rule 10): "A statement whose category is location goes in non_scoreable_requirements regardless of the job description
  heading it appears under, and never in informational_items or post_hiring_conditions." The inserted span (197 bytes, sha256 `d01b159f…81d9`)
  is the only difference. Its identity is tested byte for byte. The COVERAGE clarification, Example 5, the OR and AND rules and the output
  contract are identical in both arms.

## Design

- Twenty calls: CONTROL call 1, ROUTED call 1, CONTROL call 2, … (ten per arm, alternating, CONTROL first).
- The same stored TC20 JD, the job context ("Technical Coordinator 20") and the same user message (sha256 `ff6b334a…240473`).
- Model `gpt-4.1-2025-04-14` (the returned model must equal it). Temperature 0.1, max_tokens 6000, JSON-object mode, timeout 90 s.
- No retries. Stop at the first failed call or the first unexpected returned model.
- Hard cost cap USD 2.00, enforced before each call. The reservation is the exact sum of the worst case of each call, printed in the dry run.
- The TC20 labels and the frozen TC20 scorer are reused unchanged. The output directory must be new or empty.

## Measures (defined before the calls)

1. **Primary outcome, per call: location retained and correctly routed.** A location condition is correctly routed when a condition with
   category `location` whose source_text contains the marker "MoNE office in Ramallah" is in `non_scoreable_requirements`, and no
   condition with category `location` is in `informational_items` or `post_hiring_conditions`. "Retained" means the marker occurs in the
   whole raw answer. P(arm) is the number of calls meeting the primary outcome.
2. **Reporting, separately:** all three reporting sentences retained and routed as `reporting_line` in `informational_items` (the same
   check as the scope experiment; the count per arm).
3. **Duty coverage, separately:** the number of calls with all 16 duty lines found.
4. **The eight frozen TC20 checks**, reported per arm (C_RESP, C_COMP, C_EXP_OR, C_FAM_OR, C_LOCAL, C_LOCATION, C_REPORTING, C_AND).
5. **Schema:** the count of calls with no schema violation (the same structural check as the scope experiment).
6. **Invented sources:** the count of items or conditions whose source_text is not in the job description after whitespace is collapsed.

## Interpretation rules (fixed before the calls)

Let D = P(ROUTED) − P(CONTROL), within this experiment's fresh calls only.

1. **Guard first (no offset).** The guards are: reporting-complete calls, duty-complete calls, schema-conforming calls and invented-source-free
   calls. If ROUTED is lower than CONTROL on any guard, the result is reported as a **regression in a guarded group**. A location gain does
   not offset it. The guard applies whatever D is.
2. **Ceiling.** If P(CONTROL) ≥ 9 of 10, the comparison is **inconclusive**: the control leaves no room to show an effect.
3. **Otherwise:**
   - D ≥ 4: the result **supports** the routing sentence helping location on this JD, exploratory only. It is not proof of causation and
     says nothing about general reliability.
   - −3 ≤ D ≤ 3: **inconclusive**. A small or absent difference is treated as inconclusive.
   - D ≤ −4: the sentence **did not help** on this JD in this run.
4. The C_* checks and the duty count are descriptive for the location question. A change in C_LOCATION is reported with its own count.
5. Historical runs (the scope experiment and earlier) are reported separately and are never pooled with the fresh calls.
6. **Exploratory.** Ten calls per arm cannot separate a moderate effect from run-to-run variance. Earlier runs of the same CLARIFIED prompt
   gave location in non_scoreable in 3 of 5 calls, so the control's baseline is uncertain. No threshold here is a significance test.
7. **No activation.** Neither arm is used or activated by this experiment, whatever the result.

## Reproducibility

Every manifest records: the sha256 of each arm's prompt and of the inserted span, the system and user hashes of each call, the labels, the
frozen scorer, the protocol, the experiment script and the git commit. The dry run prints the reservation without any network call.
