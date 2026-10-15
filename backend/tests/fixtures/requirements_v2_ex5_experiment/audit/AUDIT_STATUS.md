# Audit of the Example 5 removal experiment (commit 4ea4323): status

**Current status: superseded by `AUDIT_EX5.md`, which is the verified audit.** The four experiment files are stored in `../run/` and verified there: manifest commit `4ea4323b206f6e32307c902d72d65e5c2a1bbff9`, protocol `5a741243…`, spend 0.412000 USD over ten calls, and the saved report reproduced exactly by the unchanged scorer.

## Corrections

- The earlier version of this document said the four experiment files had not been attached. That was wrong for the files attached later; the earlier files were the TC20 repeat's files, which are stored separately under `../../requirements_v2_tc20_repeat/run/`.
- Reporting: FULL kept the reporting statement in 4 of 5 calls and VARIANT in 0 of 5 (whole-answer check). Removing Example 5 did not improve reporting or OR/AND handling. This weakens the hypothesis that Example 5 causes the omission; it does not establish causation.
- The coverage wording is a **possible scope ambiguity**, not a proven contradiction or cause. Rule 9 still explicitly requires routing: "Each statement goes in exactly ONE of three lists."
- FULL's reporting result varies across runs (1 of 5, 0 of 5, 4 of 5 in three separate runs of the same prompt), so the five-call comparison is not a stable estimate.

No paid call, prompt, label, scorer, frozen artifact or production setting was changed in this audit. v2-5 stays inactive.
