# requirements-v2: rules for the future API / worker stage

Nothing here is wired yet. The package is pure functions over plain dicts; these notes record what the code that
calls it MUST do. Nothing in this file is implemented by routers, workers, migrations or the UI in the current stage.

## Server-owned state

The document carries two blocks the client must never be able to write:

| Key | Created by | Kept across a save by |
|---|---|---|
| `scoring_confirmation` | `confirm_no_numeric_score` | `carry_confirmation` |
| `classification_review` (warnings + acknowledgments) | the extraction (warnings), `acknowledge_classification_warning` (acknowledgments) | `carry_classification_review` |

`carry_server_owned(stored, incoming)` applies both. Every save MUST:

1. read `stored` from the database inside the same locked transaction (never from the request),
2. build the document to validate and persist with `carry_server_owned(stored, incoming)`,
3. run `reconcile_classification_review` on the result and audit-log what it reports (`resolved`, `invalidated`),
4. run `validate_final`, then `compute_readiness(doc, require_classification_acknowledgment=<policy>)`.

The hashes inside these blocks are unkeyed digests. They detect change; they do not prove who acted. A matching hash
in client input is never authorization, and `validate_final` accepting a document is not evidence that a block is
genuine -- never run it on unprocessed client input.

## Classification-warning policy (admin setting)

* Key: `job_analysis.require_classification_acknowledgment` (`system_config`, values `true` / `false`).
* Default Yes. A missing, empty or unrecognised value means Yes (`parse_acknowledgment_policy`, fail closed).
* The policy is read by the caller and passed explicitly to `compute_readiness` and `confirm_no_numeric_score`; the
  package never reads configuration itself.
* Yes: an unresolved classification warning keeps readiness at `needs_classification_review`.
  No: warnings stay visible (`Readiness.open_warning_ids`, `unresolved_warning_ids`) and never block.
* Only the three classification codes (`CLASSIFICATION_WARNING_CODES`) are covered. No other extraction warning may
  be given this behaviour without an explicit decision.
* Preferred items never carry a weight and never contribute to a numerical score, under either policy and after
  acknowledgment.
* Open question for the API stage: platform-wide only, or a per-tenant override of the same key.

## Endpoints (to be built)

| Action | Who | Notes |
|---|---|---|
| Acknowledge one classification warning | admin, HR manager | one warning per call; `acknowledge_classification_warning(doc, warning_id, user_id=..., acknowledged_at=...)` with the authenticated user and a server timestamp; refuse when the job is not editable |
| Correct a classification | admin, HR manager | normal item edit (`set_importance`); the warning resolves at reconcile |
| Change the policy setting | admin only | audit-logged with old and new value; takes effect for the next readiness computation |

## Audit-log events (names are proposals)

* `requirements_classification_acknowledged` -- job id, warning id, code, item id, item state hash, user, time
* `requirements_classification_ack_invalidated` -- job id, warning id, reason (`item_or_evidence_changed` / `warning_inactive` / `warning_resolved`)
* `requirements_classification_warning_resolved` -- job id, warning id, resolution (`item_removed`; a reclassified item's warning is only inactive and reopens if the item returns to Preferred)
* `requirements_classification_policy_changed` -- old value, new value, user
* `requirements_preferred_only_confirmed` -- as for the existing confirmation

## Concurrency

Use the revision-checked, row-locked save described for the requirements document: two recruiters acknowledging or
editing at once must not overwrite each other's block. `reconcile_classification_review` is idempotent.
