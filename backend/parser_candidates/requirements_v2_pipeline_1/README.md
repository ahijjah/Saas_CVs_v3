# requirements-v2 extraction pipeline 1 (candidate, offline)

One entry point and one result contract over four components, none of which is modified:

| component | version | role |
|---|---|---|
| frozen parser `services.requirements_v2.extraction.parse_response` | benchmark commit `059c56b` | parse + validate the model's JSON, build the draft and the original snapshot |
| injection guard | `requirements-v2-injection-guard-1.1` | AI-directed text in the JD turned into a requirement or a weight |
| split-OR guard | `requirements-v2-split-or-guard-1` | one OR requirement split into one item per option |
| warning adapter | `requirements-v2-warning-adapter-1` | model warnings preserved, item-specific Required/Preferred conflicts linked |

Pipeline `requirements-v2-pipeline-1`, contract `requirements-v2-pipeline-result-1`. **Not wired** into any API, worker or UI. No model call, network or database.

## Operations (all pure; the caller persists the returned state)

```python
from parser_candidates.requirements_v2_pipeline_1 import extract, reconcile, acknowledge, confirm_structure, confirm_no_numeric_score, evaluate, validate_state

state = extract(jd_text, raw_model_text, finish_reason="stop", extraction_prompt={"version": ..., "sha256": ...}, require_classification_acknowledgment=True)
state, events = reconcile(stored_state, edited_document, user_id=..., at=..., client_state=<ignored>, require_classification_acknowledgment=...)   # EVERY edit
state = acknowledge(stored_state, "classification" | "conflict", warning_id, user_id=..., at=..., require_classification_acknowledgment=...)
state = confirm_structure(stored_state, item_id, user_id=..., at=...)               # the frozen confirmation
state = confirm_no_numeric_score(stored_state, user_id=..., at=...)                 # refused unless pipeline readiness is needs_confirmation
state = evaluate(stored_state, require_classification_acknowledgment=...)           # recompute every derived part (e.g. after the admin changes the policy)
problems = validate_state(state, jd_text=None)                                       # [] = well-formed; with jd_text also re-detects guard issues from the stored raw text
```

`PipelineError.code`: `extraction_failed`, `not_acknowledgeable` (injection, split_or, structure, confirmation, validation, no_items, extraction), `unknown_gate`, `not_ready_for_confirmation`.

## Result contract (a plain JSON object)

| key | content |
|---|---|
| `contract_version`, `component_versions` | pipeline, contract, parser (frozen commit), extraction prompt `{version, sha256}` as recorded by the caller (the frozen parser itself only knows v2-1), the three guard versions |
| `ok`, `status`, `errors` | the frozen parser's verdict; `ok=false` → readiness `extraction_failed`, nothing else is populated |
| `job_description_sha256` | hash of the JD the state belongs to |
| `raw_response` | `{text, sha256, finish_reason}` — the model's text exactly as received |
| `raw_ai_output` | the parsed JSON object exactly as the parser kept it (including warnings the frozen parser dropped) |
| `requirements` | the current document (the editable draft; server-owned blocks `scoring_confirmation`, `classification_review`, `structure_review` inside) |
| `original`, `original_digest` | the snapshot of the draft as extracted (never changes) |
| `extraction` | scoreability, proposed category weights, conditions, unmapped, parser review items, warnings the frozen parser kept |
| `review_records` | **server-owned** `injection`, `split_or`, `model_warnings` (detected issues, resolutions, acknowledgments, history) |
| `policy` | the policy used for the top-level readiness |
| `readiness` | `{state, can_proceed, scoring_mode, reasons, policy_ack_required, by_policy:{ack_required, ack_not_required}}` — **derived** |
| `gates` | per gate: open count and whether it blocks under each policy — derived |
| `unresolved_issues` | one list in precedence order: `{gate, id, kind, message, item_ids, category, blocks_when_ack_required, blocks_when_ack_not_required, resolution_options}` — derived |
| `normalized_warnings` | every model warning, normalized (string or object, malformed kept), with kind, linked items and limitation — derived mirror |
| `informational` | similarity warnings, parser review items, generic model notes — never block |

## Precedence and policy

`extraction failed / invalid document (needs_review) > injection > split-OR > classification review [policy Yes] > model importance conflict [policy Yes] > structure review > preferred-only confirmation > ready` (`needs_items` for an empty document).

* Policy **Yes** (`require_classification_acknowledgment=true`): classification warnings and importance conflicts block until acknowledged or corrected.
* Policy **No**: they stay visible (`unresolved_issues`, `blocks_when_ack_not_required=false`) and never block.
* Injection, split-OR, structure review and the preferred-only confirmation do not depend on the policy. Generic model notes, duplicate notes and ordinary similarity warnings are informational.

## Why stale or client-supplied state cannot unblock

1. Readiness, gates and the issue list are never stored as authority: `evaluate` recomputes them from the current document and the records on every call.
2. Each guard derives openness from the document (an issue is open when the implicated item exists / the contaminated weight is applied / the split items are still there), not from a stored status. Forged "resolved" fields change nothing.
3. `reconcile` rebuilds every server-owned part from the **stored** state: the document's confirmation / classification / structure blocks come from the stored document (`_carry` is tested equal to the frozen `carry_server_owned`), the three records from the stored records. The client's blocks, records, readiness and `client_state` are discarded.
4. Acknowledgments are accepted only by `acknowledge`, only for classification and conflict, and are invalidated when the item or its evidence changes. Acknowledging one gate never changes another.
5. `confirm_no_numeric_score` checks the **pipeline** readiness (the frozen function only checks the frozen one).
6. Deleting issues from a stored record is detectable with `validate_state(state, jd_text)`.

## Replay of the 48 stored responses

`python scripts/requirements_v2_pipeline_replay.py --out benchmark_results/requirements_v2/pipeline_replay` → `REPORT.md`, `report.json`. Each prompt's totals cover both runs (run1 + run2 = 24 calls per prompt, 48 in all). Readiness and unresolved issues per call and policy are reported separately from the official gates, which are recomputed by the frozen scorer and compared with the saved `results.json` (identical). The pipeline's readiness is not a benchmark score.

## Agreed decisions carried forward (not open)

* **Who may correct or acknowledge:** tenant admins and HR managers (the existing `can_edit` / `ensure_can_edit` rule), subject to the existing job access check (job of the caller's own tenant; another tenant's job answers 404). No new role is introduced.
* **No acknowledgment bypass** for injection and split-OR issues, under either policy. They are cleared only by correcting the document (remove the item; correct the contaminated category weight; keep one item with the full alternatives or remove the split items).
* **The original AI output and the original snapshot are preserved** and never edited: `raw_response`, `raw_ai_output`, `original`, `original_digest` are set once by `extract`. The existing job-level `original_analysis_json` stays the source of the snapshot.

## Integration steps (not done)

The existing endpoints in `routers/job_requirements.py` and `services/requirements_api.py` (`_mutate`, `plan_save`, `acknowledge_warning`, `confirm_no_score`, `confirm_structure_review`) keep their transaction skeleton unchanged. The pipeline replaces the pure planning step and adds the guard records; it does not change who may write or how the write is protected.

1. **Storage.** The guard records (`review_records`), `raw_response` and the pipeline `component_versions` are server-owned data of the job's requirements. They need a home next to the existing document (decision below). `readiness`, `gates`, `unresolved_issues`, `normalized_warnings` and `informational` are derived on every read and need not be stored.
2. **Worker.** After the model call, pass the raw text and `finish_reason` to `extract(...)` instead of `parse_response`, record the real prompt `{version, sha256}`, and persist the state in the same way the analysis is persisted today.
3. **GET.** Return the stored state evaluated with `evaluate(state, require_classification_acknowledgment=<admin setting read at request time>)`, so a policy change takes effect without a new extraction.
4. **PUT (save).** Keep the whole existing transaction:
   * `ensure_can_edit(role)` (admin / HR manager), then `_load(..., lock=True)`: tenant row-level-security context, job access check, **`SELECT ... FOR UPDATE` row lock**, v2 / marker / original / migration-column checks;
   * **`expected_revision` check** (409 with the current view on mismatch) and the revision-checked `UPDATE`;
   * **audit entries written in the same transaction** with `strict=True`; any failure rolls everything back.
   Inside it, replace `plan_save` by the pipeline reconcile: build the incoming document exactly as today (`build_incoming`: unknown/server-owned client fields discarded, ids reserved), then `reconcile(stored, incoming_doc, user_id, now, require_classification_acknowledgment=policy)`. Ignore every other field of the request body.
   * **`validate_final` is preserved unchanged.** A document with invalid structure or weights is refused with **422 (`CODE_INVALID`) and nothing is written**: no document, no record, no audit, no revision bump. The pipeline's `validation` gate is only the read-side view of the same check; it must not become a different rule.
   * **A valid document with unresolved review blockers is saved** (200, revision + 1, audit written) like any other valid edit. Injection, split-OR, classification, conflict, structure and confirmation issues do **not** make a save fail; they make `readiness.can_proceed` false. Saving incomplete review work in several steps must stay possible.
   * Audit: the existing `saved` / `structure_recorded` / `warning_resolved` / `acknowledgment_invalidated` / `structure_invalidated` / `confirmation_invalidated` actions stay; the events returned by `reconcile` (component `injection_guard`, `split_or_guard`, `warning_adapter`) are added as new audit actions in the same transaction. Names of the new actions are an implementation detail to settle with the audit schema.
5. **Acknowledge endpoint.** Same transaction skeleton; body `{expected_revision, gate: "classification"|"conflict", warning_id}`; call pipeline `acknowledge`; `not_acknowledgeable` / `unknown_gate` → 409, unknown warning → 404 (as today).
6. **Structure / confirmation endpoints.** `confirm_structure`, `confirm_no_numeric_score` through the same skeleton (409 on `not_ready_for_confirmation`).
7. **Proceed gate.** A requirements-v2 job may be used for scoring only if `evaluate(state)["readiness"]["can_proceed"]` is true for the current policy; this is a separate check from saving (candidate evaluation of v2 jobs is still refused by `services/requirements_guard.py` today and stays so until that gate is wired).
8. **UI.** Show `unresolved_issues` grouped by gate in precedence order with `resolution_options`; injection and split-OR issues have no acknowledge control; classification / conflict show an acknowledge action only when `blocks_when_ack_required`; list `normalized_warnings` and `informational` as read-only; keep the original/draft comparison. A saved-but-blocked document shows its blockers and a disabled proceed action.
9. **Tests to add at that point:** API-level equivalents of `tests/test_requirements_v2_pipeline.py` (forged body, stale body, 422 with no write, valid-with-blockers saves, role/tenant checks, revision conflict, policy flip, audit rollback).

## Unresolved decisions

* Where the guard records and raw response live (existing JSON column vs. new storage → possible migration) and their retention (`raw_response` may contain personal data if real job descriptions do).
* Whether injection / split-OR false positives need an additional escape path beyond editing or removing the item (today none, deliberately).
* Position of the conflict gate (currently after the frozen classification review, both policy-governed).
* The frozen parser pins prompt v2-1 for its own bookkeeping; the real prompt is recorded by the caller. Prompt v2-2 is not registered or activated, and there is no held-out benchmark set yet for the prompt or the guards (the detection rules were developed against the 12 benchmark job descriptions).
* Names of the new audit actions.
