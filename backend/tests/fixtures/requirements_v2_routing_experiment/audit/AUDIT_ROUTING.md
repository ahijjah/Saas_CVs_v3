# Audit of the location-routing experiment (commit c55025d)

Status: **provenance verified; usage and costs verified; scored.json reproduced exactly by the unchanged scorer; all 20 raw answers checked
for reporting and location.** No paid call, prompt, label, scorer, frozen artifact or production setting was changed. Both arms stay
inactive. Historical runs are kept separate (see `../../requirements_v2_decision/DECISION_REPORT.md`).

## 1. Files and provenance (stored byte for byte in `../run/`)

| File | sha256 |
|---|---|
| `calls.jsonl` (20 rows) | `d221db006fcd32d6eaad8fd8142be860bc3712480cd0791e5e450b1371880bb9` |
| `manifest.json` | `e7b17cf008c22722f6ac7f440d0619c05d9caf0925fbcd94886b4b57e1dbad8f` |
| `run.json` | `538c5bd7c5ecc2b19a1e07f1e3527d4476d28a2e44c5443a2f6b86b54d951323` |
| `scored.json` | `29b4d8c21d471970c06a11c856d2865d3cb42559456ef249c3d7ca470f7e2098` |

- Manifest: git commit `c55025d29bdef1d4cb238c876c252d85f0f8df33`; protocol `73fa404f…25fff8` (equal to the file); routing script
  `22b39be3…5f3ee6`; scope script `b95290af…14e09ad`; CONTROL `266329fc…d61103e` (30,550 bytes); ROUTED `f5042e34…56d5` (30,747 bytes);
  inserted span `d01b159f…81d9` (197 bytes); user message `ff6b334a…240473`.
- Every pin equals the current file in this repository.
- Twenty rows, alternating, CONTROL first. Each row: its arm's system hash, the user hash, model `gpt-4.1-2025-04-14` (requested and
  returned), finish `stop`, no error.

## 2. Usage and costs

- Per-call cost equals `prompt_tokens × 2e-6 + completion_tokens × 8e-6` (rounded to six places) in all 20 rows.
- Spend **0.709932 USD** = `run.json` = `scored.json`. CONTROL 0.361516; ROUTED 0.348416.
- Reservation 1.65862 USD under the 2.00 cap (spend is 42.8 % of the reservation).

## 3. Reproduction

`requirements_v2_routing_experiment.report(rows, labels, jd)`, with the frozen TC20 labels and scorer, returns the saved `scored.json` report
in full. `run.json` equals `scored.json["run"]`.

## 4. Reporting against the whole raw answer

The three reporting sentences were searched in every raw answer (case-insensitive on both sides). In this batch reporting is **never partial**: each
answer has all three or none. (Across all batches, one answer is partial; see the decision report.) The whole-answer check agrees with the scored flags in all 20 calls.

| Arm | All 3 present (of 10) | None present | Partial |
|---|---|---|---|
| CONTROL | 6 | 4 | 0 |
| ROUTED | 3 | 7 | 0 |

Where present, all three are routed as `reporting_line` in `informational_items`. The second reporting item merges two statements (the
"in coordination with" sentence and "maintain regular communication"), as in the earlier audit. Per call (order: present = yes/no):
CONTROL present at calls 3 (order 5), 4 (order 7), 5 (order 9), 8 (order 15), 9 (order 17) and 10 (order 19); the rest absent. ROUTED present
at calls 6 (order 12), 8 (order 16) and 10 (order 20); the rest absent.

**Interpretation.** The ROUTED arm has 3 of 10 against 6 of 10. The difference is a guard regression in this batch. Fisher's exact test
gives two-sided p = 0.37. The batch cannot show that the added sentence caused the drop: the two arms differ in one sentence, but the
control's own reporting result has varied across batches (section 4 of the decision report).

## 5. Location, all 20 calls

- Location is **retained and correctly routed in 20 of 20 calls** (10 of 10 in each arm). In each call the location condition is in
  `non_scoreable_requirements` with category `location`, and no location condition appears in `informational_items` or
  `post_hiring_conditions`.
- Frozen check `C_LOCATION`: 10/10 in both arms.
- No added benefit can be shown: the control was already at ceiling in this batch. The earlier CLARIFIED batch had location in 3 of 5, so the
  same prompt bytes have produced both 3 of 5 and 10 of 10 in different batches.

## 6. Duties, OR, AND, schema and sources

- **Duties:** 16 of 16 in all 20 calls. Duty-complete counts: CONTROL 10/10, ROUTED 10/10.
- **OR:** the frozen `C_EXP_OR` and `C_FAM_OR` checks fail in all 20 calls (0/10 in each arm). The ICT and familiarity alternatives are not
  kept as lists in any answer of this batch.
- **AND:** the communication line is one item in all 20 calls; `C_AND` fails 20 of 20.
- **Schema:** all 20 answers conform to the output contract. **Invented sources:** none in any answer (each source text occurs in the JD).
- **Warnings:** empty in all answers, as in the earlier runs.

## 7. Scored readings

- `primary_reading`: "regression in a guarded group (reporting_complete): reported; the location result does not offset it". The protocol
  applies the guard before the location difference (here 0). This is a reported regression in this batch. It is not a finding that the
  added sentence caused it.

## 8. Limits of this audit

- Ten calls per arm from one JD: a difference of three to four calls is within the variation seen for the same prompt across batches.
- The reporting check is the whole-answer, three-sentence check defined in the protocol (stricter than the frozen `C_REPORTING`, which
  checks the labelled sentence only).
- Nothing here is a general reliability claim, and nothing is activated.
