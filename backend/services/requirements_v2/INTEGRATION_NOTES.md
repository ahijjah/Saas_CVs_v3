# requirements-v2: rules for the API / worker stages

The package is pure functions over plain dicts. The editing / review API stage is implemented in
`services/requirements_api.py` + `routers/job_requirements.py` (see "Editing API (implemented)" at the end). Workers,
extraction wiring, job creation and the UI are NOT wired.

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


## Editing API (implemented in the API stage; migration 107 prepared, NOT applied)

| Endpoint | Who | Notes |
|---|---|---|
| `GET /jobs/{id}/requirements` | any user with job access | current document, original snapshot, readiness, classification warnings, per-category Edited flags, `can_edit`; no lock, no write |
| `PUT /jobs/{id}/requirements` | admin, HR manager of the job's tenant | body `{expected_revision, requirements}`; validated by `validate_final`; 422 with issues, nothing stored |
| `POST .../classification-warnings/acknowledge` | same | body `{expected_revision, warning_id}`; user and time are server-stamped |
| `POST .../confirm-no-numeric-score` | same | body `{expected_revision}` |

Persistence: document in `job_criteria.analysis_json["requirements"]`; original in `original_analysis_json["requirements"]`
(never written by the API); `requirements_revision` (+1 per write) and `requirements_retired_item_ids` (append-only) from
migration 107; the seven `weight_*` columns are rewritten from the document in the same UPDATE. One transaction:
`SELECT ... FOR UPDATE` -> access/role -> revision check -> UPDATE (guarded by `requirements_revision = :rev`) -> strict
audit rows -> commit. Audit actions: `requirements_saved`, `requirements_classification_acknowledged`,
`requirements_classification_ack_invalidated`, `requirements_classification_warning_resolved`,
`requirements_preferred_only_confirmed`, `requirements_preferred_only_confirmation_invalidated`, and (platform config)
`requirements_classification_policy_changed`.

Policy: `system_config` key `job_analysis.require_classification_acknowledgment` (seeded `true` by 107), platform-wide, changed
only via `PUT /admin/platform-config/{key}` (super_admin); no tenant override. Read inside each request; a missing or
unrecognised value means Yes.

Moving an item between categories is still refused (422): outside the structure stage.

## Structured requirements (OR alternatives, experience subject / duration)

Recruiters may edit the structured fields of existing items and supply them on new items (`PUT .../requirements`); they are stored
verbatim, never interpreted. Nobody and nothing verifies that wording and structure agree. An item with structure is **settled** when
its (wording, alternatives, experience) equal the original AI analysis, or a person confirmed the current triple (`confirmed`), changed
its structured fields in a save (`corrected`) or created it with structure (`entered`). Otherwise -- e.g. after a wording-only edit --
readiness is `needs_structure_review` (blocks under either classification policy; classification review is evaluated first).
Saving the same structured values again creates no record. `POST .../structure-review/confirm {expected_revision, item_id}` records
the confirmation (server-stamped user and time, wording + structure confirmed). Records live in `document["structure_review"]`
(server-owned, `carry_structure_review`), are pruned at the first save after the wording or structure changed (audit reason
`item_changed` / `item_removed`) and never come back on their own. A structure change also invalidates the classification
acknowledgment (it is part of the acknowledged item state) and the preferred-only confirmation (part of its basis hash).
Audit actions: `requirements_structure_confirmed`, `requirements_structure_recorded`,
`requirements_structure_confirmation_invalidated`.
