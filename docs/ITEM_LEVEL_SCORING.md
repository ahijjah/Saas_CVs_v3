# Item-level scoring (Requirements V2)

For a job with `requirements_schema_version = 2` a CV is scored requirement by requirement. Legacy jobs keep the earlier category-level
scoring unchanged. The model decides only a **result and evidence** for each item; every number is computed by code (`backend/services/item_scoring/calc.py`).

## The model

| Required item result | Factor | Meaning |
|---|---|---|
| `met` | 100% | the CV clearly satisfies the complete requirement |
| `partially_met` | 50% (fixed, not configurable) | the CV clearly satisfies only part of it |
| `not_met` | 0% | reliable evidence (a quotation) that it is not satisfied |
| `unclear` | no automatic score | evidence missing, ambiguous, conflicting or unverifiable; a person resolves it |

Preferred items are `yes` / `no` / `not_clear`. They have no weight, never enter any number, never affect eligibility, and `not_clear` never blocks a decision.

```
earned(item)          = item_weight x factor                    (Required item weights total 100 in a category)
category_score        = sum of earned(item)                     (0..100)
category_contribution = category_score x category_weight / 100
final_score           = sum of category contributions           (0..100)
```

* All arithmetic uses exact `fractions.Fraction`; there is no binary floating point. With whole-number weights and the 1/2 factor every contribution has an exact three-decimal form (`exact_decimal` refuses to round).
* **Rounding rule (one, documented):** the FINAL score only is rounded, once, to a whole number, **half up** (73.5 -> 74, 84.1 -> 84). Items, categories and contributions are never rounded; the view shows their exact values (`84.1`, `26.5`).
* The stored decision compares the rounded final score with the qualified and partial thresholds (`threshold_service`, defaults 85 / 65; system -> tenant -> job cascade).
* The model's `evaluation_notes`, strengths, gaps and questions are kept as narrative; any number it supplies is ignored.

### Evidence rules

Every decisive result (`met`, `partially_met`, `not_met`, `yes`) needs at least one quotation that really occurs in the extracted CV text (NFKC, case, whitespace and Arabic diacritics ignored). A result whose quotations do not verify is downgraded to `unclear` / `not_clear` and the explanation says why. A decisive result with confidence below 0.60 (or no readable confidence) is also downgraded. Missing evidence is `unclear`, never `not_met`. A failed model call or unreadable output is a **processing error** (Celery retry, max 3): it is never an `unclear` item, a rejection or a score.

## Unclear Required items

While any Required item is `unclear` the scoring status is `review_required`: **no final score and no decision are stored**. The provisional range counts each unclear item as 0 (minimum) and as its full weight (maximum). An admin or HR manager resolves the item to Met / Partially Met / Not Met with a required reason; the resolution stores user, time, previous and new result, reason, and an append-only event; the score is recalculated in the same transaction and the decision is stored when the last unclear item is resolved. A resolved item cannot be resolved again.

A job with no Required items is `not_scored`: the score is absent (null, not zero), the decision is not stored, the application is not Rejected, and the Preferred results are still shown.

## Mandatory Gates

A Mandatory Gate on a CV-evidence requirement follows the **same stored item evaluation** (no second evaluation): `met` -> passed; `partially_met` or `not_met` -> Not Eligible; `unclear` -> Review Required. A processing error is a retry state, never Not Eligible. Gate outcomes never change the numeric score or the stored scoring decision; they change only the **effective decision**:

```
not_eligible      a gate failed
review_required   a gate needs verification, or an unclear Required item is unresolved
not_scored        no Required item
otherwise         the stored decision (qualified / partial / rejected)
```

The effective decision is one rule in Python (`eligibility_service.effective_decision`) and SQL (`services/effective_decision.py`) used by lists, filters, exports, job and campaign counts and the candidate screens. Manual gate overrides, authorization and audit are unchanged. Knockout Questions remain a separate candidate-answer mechanism with no numeric contribution.

## Revisions and rescoring

Each run stores the exact `requirements_revision` it used and a snapshot of the category weights; the write is revision-safe (`lock_revision`). When the job's revision moves on, the earlier score is **stale** (derived, never rewritten) and the screen says "Scored against revision X; current revision is Y." An admin or HR manager can rescore one application (`POST /applications/{id}/item-scoring/rescore`); the previous run and its evaluations stay as history (`is_current = false`). Nothing rescoring is automatic or bulk.

## Gatekeeper

For a V2 job the local whole-description similarity ("gatekeeper") is a routing hint only: it never rejects, never gives a score of zero and never skips item evaluation or gates (a "clear mismatch" is routed to the evaluation like an uncertain case; the hint is stored on the run). Retained for V2 as before: security blocking (prompt-injection / security check) and duplicate detection. A legacy job keeps its local rejection.

## Compatibility and storage

* Migration `103_item_level_scoring.sql` is additive: `application_scoring_runs` (one current run per application), `application_item_evaluations` (one result per item per run), `application_item_evaluation_events` (append-only), and `applications.scoring_status`, `item_final_score`. All have tenant RLS, worker policies and runtime grants (`runtime_grants.py`, `privilege_manifest.json`).
* Legacy scores in `application_scores` are untouched and stay readable; the API labels them `legacy_category` and the screens say so. They are never reinterpreted as item results. An application scored at item level has no `application_scores` row.
* `applications.item_final_score` / `scoring_status` mirror the run so lists and filters stay cheap; the run tables remain the source of truth, and the detail view recomputes every number from the stored item results.

## API

| Route | Roles |
|---|---|
| `GET /applications/{id}/item-scoring` | every tenant role |
| `POST /applications/{id}/item-scoring/items/{item_id}/resolve` `{result, reason}` | admin, HR manager |
| `POST /applications/{id}/item-scoring/rescore` | admin, HR manager |

Another tenant's application is the same 404 as a missing one. Audit rows (`item_result_resolved`, `item_rescore_requested`) carry ids and results, never reason text or CV content.

## Rollback

`backend/db/rollback/103_item_level_scoring.down.sql` removes exactly what 103 added (it is not a migration; the runner never reads it and the ledger row is not removed). Run it on a restored copy first, and only together with deploying code that predates 103. It loses item-level runs, evaluations, resolutions and history; applications, legacy scores, stored decisions, gate results and requirements documents are untouched. After it, an application scored at item level shows as scored without a numeric score until it is scored again. The normal rollback remains restoring the pre-upgrade backup; the migration test (`test_item_scoring_migration_postgres.py`) applies the script and re-applies 103.

## Known limits

* The confidence floor (0.60) and the half-up rounding are product rules fixed in code, not per-tenant settings.
* Item evaluation is one model call per CV.
