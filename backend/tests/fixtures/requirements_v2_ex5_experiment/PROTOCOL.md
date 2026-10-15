# Example 5 removal experiment: protocol and interpretation rules

Recorded BEFORE any call. Its sha256 is pinned in `scripts/requirements_v2_ex5_experiment.py` and written into the run manifest.
Starting point: commit `9180fe0bf160e0be0e5de4577a999398420c2d06` (the repeat audit).

## Question

Does the worked Example 5 in `criteria_extraction_v2-5` contribute to the reporting omission observed in v2-5 (the reporting
statement absent from the whole answer in 9 of 10 historical calls)? Hypothesis H1.

## Arms (fresh calls only)

- **FULL**: `criteria_extraction_v2-5` as it is (sha256 `f1569a8b…f1569a8b`, the existing offline candidate).
- **VARIANT**: `criteria_extraction_v2-5-noex5` (sha256 `7cf171c8…66e62`). It is the exact byte prefix of FULL before the Example 5
  header; the removed span (Example 5, 3,223 bytes, sha256 `33bdd1dc…e31e6`) is the only difference. Its identity is tested.

## Design

- Ten calls: FULL call 1, VARIANT call 1, FULL call 2, VARIANT call 2, … (five per arm, alternating, FULL first).
- The same stored TC20 JD, the VPS job context ("Technical Coordinator 20") and the same user message (sha256 `ff6b334a…240473`).
- Model `gpt-4.1-2025-04-14` (the returned model must equal it). Temperature 0.1, max_tokens 6000, JSON-object mode, timeout 90 s.
- No retries. Stop at the first failed call or the first unexpected returned model. Hard cost cap USD 1.00, enforced before each call.
- The labels and the TC20 scorer are unchanged and are reused. The output directory must be new or empty.

## Interpretation rules (fixed before the calls)

1. **Reporting improvement supports the contribution; it does not prove causation.** Let R(arm) be the number of calls whose whole raw
   answer contains the labelled reporting sentence ("submit deliverables"). Let D = R(VARIANT) − R(FULL), within this experiment.
   - D ≥ 3: the result **supports** Example 5 contributing to the omission. It does **not** prove causation: ten calls, two arms,
     run-to-run variance and possible interactions with other instructions remain.
   - 1 ≤ D ≤ 2: **inconclusive**.
   - D ≤ 0: **weakens** the hypothesis. It does **not** rule out an interaction with other instructions (for example the coverage
     paragraph), or a cause unrelated to the examples.
2. **Compare the two fresh arms directly.** Every comparison uses this experiment's calls only. The historical runs (the paired run and
   the repeat) are reported in their own section, labelled as history, and are never pooled with the fresh calls.
3. **Report five groups separately**, each with its own counts: reporting coverage (whole answer, and `informational_items`); duty
   coverage (of 16 lines); OR alternatives (the ICT list and the familiarity list); AND splitting (the communication, coordination and
   teamwork line); and the eight frozen checks. A gain in one group does not offset a failure in another.
4. **Scale.** Five calls per arm cannot separate a difference from run-to-run variance. The control's own C_EXP_OR moved from 0 to 4 of 5
   across two runs of the same prompt, so OR differences are reported as descriptive only, and C_REPORTING differences likewise.
5. **Nothing is promoted.** v2-5 stays inactive regardless of the result. A result here does not decide whether v2-5 is used.

## Reproducibility

Every run manifest records the prompt sha256 of each arm, the user-message and system-message hashes of each call, the labels, the
protocol, the experiment script, the variant identity, and the commit. The reservation is printed in the dry-run.
