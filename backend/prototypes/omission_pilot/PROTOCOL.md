# Pre-registered omission pilot: CONTROL against INVENTORY (TC20)

Status: pre-registered before any call. **No API call has been made and none is authorised by this document.** A paid run needs a separate
owner approval that names this protocol's manifest hash and an approved cap at or above the reservation in section 8.

## 1. Question
Does adding the reviewed source-unit inventory block (commit 10ea044) to the pinned v2-3 request keep the 16 TC20 duty lines, without
regressing any guard? This is a descriptive, guard-gated pilot. It is not an accuracy claim, and it cannot show an improvement on its own.

## 2. Arms
- **CONTROL**: the pinned v2-3 request. System prompt sha256 `21a2f9420c12…` (criteria_extraction_v2-3). User message sha256 `ff6b334ad323…`
  (job context plus the TC20 JD between markers).
- **INVENTORY**: the same request, plus the exact marked inventory block produced by `preview.addition_block` (10ea044). The block is
  appended to the user message after a blank line. Nothing else differs.

## 3. Fixed for both arms (must be identical)
- Model `gpt-4.1-2025-04-14` (the snapshot the TC20 repeat batch used). The returned model must equal it, or the run stops.
- temperature 0.1, max_tokens 6000, response_format `json_object`, timeout 90 s, retries 0.
- The TC20 JD (sha256 `c0c7132c6bd1…`), its reviewed heading and background annotation (`annotations/tc20.json`), the job context, the
  output schema of the baseline. The inventory block adds no output field.

## 4. Outcomes
1. **Primary**: a call *retains* the duty lines when all 16 TC20 duty lines appear as substrings of `from_responsibilities` items'
   `source_text` (the frozen C_RESP semantics). Reported as the number of calls, per arm, that retain all 16.
2. **Secondary**: review-candidate spans (`genuine_omission`, word-bearing) across the 35 reviewed requirement units. Total word-bearing
   spans are also reported. The secondary decision uses only review candidates, because intro wording and paraphrase are not missing content.
3. **Reported separately, never added into a score**: intro wording, paraphrases, punctuation-only spans, review candidates, and the
   coverage counts (quoted units, all-text-quoted units, omitted and partial units).

## 5. Guards (paired per call, reported separately)
- OR: C_EXP_OR and C_FAM_OR (frozen).
- AND: C_AND (frozen).
- Category: C_LOCAL (frozen).
- Location: C_LOCATION (frozen).
- Reporting: C_REPORTING (frozen).
- Schema: valid JSON object with all seven top-level keys, the seven category arrays, and list types for the lists.
- Invented content: every item's `source_text` is verbatim in the JD (`source_not_verbatim = 0`) and every `from_responsibilities`
  item is a duty line (`duty_not_a_duty_line = 0`), both as in the frozen scorer.

A **guard regression** is a pair in which CONTROL passes a guard and INVENTORY fails it. Any guard regression blocks a favourable reading,
however much coverage improves. Coverage cannot offset a guard regression.

## 6. Design, stop rules, thresholds (fixed now)
- **Order**: alternating, CONTROL first: C1, I1, C2, I2, … C10, I10. Twenty calls. No call is repeated or replaced.
- **Stop** at the first failed call: an exception, an empty answer, or `finish_reason` other than `stop`. Also stop when the returned model
  differs from the pinned snapshot. There are no retries. The run then reports `INCOMPLETE` and draws no verdict.
- **Cap guard**: the run stops before any call whose worst-case reservation would take cumulative reservation above the approved cap.
- **Verdicts** (evaluated in this order; the first match applies):
  1. `BLOCKED_GUARD_REGRESSION`: total paired guard regressions > 0.
  2. `PRIMARY_REGRESSION`: INVENTORY calls retaining all 16 are fewer than CONTROL's.
  3. `PRIMARY_AND_SECONDARY_SIGNAL`: INVENTORY retains 16/16 in all 10 calls, CONTROL loses at least 1 call, and the median review-candidate
     count for INVENTORY is at least 2 lower than for CONTROL.
  4. `PRIMARY_SIGNAL_ONLY`: the first condition of 3 holds but the secondary condition does not.
  5. `NO_PRIMARY_DIFFERENCE`: otherwise.
  The constants are `PRIMARY_MIN_CONTROL_LOSSES = 1`, `PRIMARY_INVENTORY_RETAINED = 10`, `SECONDARY_MEDIAN_REDUCTION = 2`,
  `GUARD_REGRESSIONS_ALLOWED = 0` in `pilot.py`. The tests check that they match this section.

## 7. Call count: why 10 pairs, not 5
Historical evidence, from the TC20 repeat batch on this same model (`tests/fixtures/requirements_v2_tc20_repeat`): v2-3 retained all 16
duty lines in 4 of 5 calls and lost them in call 5; v2-5 retained them in 5 of 5. One loss in five gives a 95% Clopper–Pearson interval of
0.005 to 0.716 for the loss rate. That is far too wide to power a rate comparison, so the count is chosen for the guard and detection
job, not for an estimate.

If the true loss rate of a condition is 0.2, the chance of seeing at least one loss in n calls is 0.672 (n=5), 0.893 (n=10),
0.965 (n=15), 0.988 (n=20). With 10 pairs, a 20% loss rate in INVENTORY is caught at least once with probability about 0.89, and a control
loss is seen with the same probability. Five pairs would miss a 20% loss a third of the time. Twenty pairs would double the cost for a
small gain in detection. Ten pairs is the chosen trade-off. The same figures are printed by `justification()` in the dry run.

## 8. Reservation and cap
- Worst case per call, from the full request: input estimated as ceil(characters / 2) (the harness convention, conservative for this text)
  at $2 per million input tokens, plus the full output ceiling of 6000 tokens at $8 per million. It is **not** the historical average.
- CONTROL request: 29,467 characters, worst case **$0.077468**. INVENTORY request: 37,265 characters (the block adds 7,796), worst case
  **$0.085266**.
- Reservation for 10 pairs (20 calls): 10 × (0.077468 + 0.085266) = **$1.62734**.
- The cap must be **at least** this reservation. The dry run states the cap as NOT APPROVED. No paid call may start until the owner
  approves a cap, which must name the manifest hash (section 9).
- Price status: the prices are the owner-supplied values recorded in `requirements_v2_tc20_compare.PRICING_STATUS`. The sandbox could not
  open the official page, so the figures are not independently verified here.

## 9. Freeze (hashed in the dry-run manifest, before any call)
Both complete requests (canonical JSON of settings and messages); the system prompt, CONTROL user and INVENTORY user hashes; the inventory
addition; the JD; the annotation; the labels and evidence files; the settings; this protocol; `pilot.py`; the pilot test file `tests/test_pilot.py`; the frozen scorer
`scripts/requirements_v2_tc20_eval.py`; the pricing module; and the replay logic (`replay.py`, `coverage.py`, `inventory.py`,
`preview.py`). The manifest hash is the hash of all of these. Any change to any of them changes the manifest hash, and an approval naming
a different hash is refused.

## 10. Execution gate
- `pilot.py` has **no CLI path to a paid run**. Its CLI supports `--dry-run` only.
- `run_pilot(client, approval)` is a library function. It needs a client object from the caller (there is no default client) and an
  approval record with the manifest hash and an approved cap at or above the reservation. It refuses otherwise, before any call.
- Tests use a fake client only.

## 11. Reporting plan
Report every call, including any stop, the reservation actually reached, the guard table per arm and per pair, the primary and secondary
counts, the separated class counts, and the verdict from section 6. Any deviation from this protocol is reported with its reason. No
threshold, guard or outcome is changed after a call.

## 12. What this pilot cannot show
- It cannot show that extraction accuracy improved in general, only on TC20 with this model and 10 pairs.
- A `PRIMARY_AND_SECONDARY_SIGNAL` is descriptive. It does not activate a prompt, enable scoring, or change production.
- Semantic completeness is not assessed by any counter here. Review candidates need human review.
