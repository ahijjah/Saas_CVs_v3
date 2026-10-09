# Production requirements-v2 pipeline service

Backend-only integration of the approved offline pipeline (`parser_candidates/requirements_v2_pipeline_1`, unchanged) into the existing editing/review API
(`services/requirements_api.py`, `routers/job_requirements.py`). No UI change, no v2 creation, no live extraction, no scoring, no migration, no backfill.

`injection.py`, `split_or.py`, `warning_adapter.py`, `text.py` are byte-for-byte copies of the approved sources apart from import paths (pinned by
`tests/test_requirements_pipeline_service.py`; change the source first). `core.py` holds the record lifecycle; its derived-view and reconcile functions are
AST-identical to the approved pipeline's. `services/requirements_v2/` (frozen at 059c56b) is untouched.

## Storage contract

`job_criteria.analysis_json`:

| key | meaning |
|---|---|
| `requirements` | the single editable document (unchanged) |
| `requirements_pipeline` | **server-owned record**, optional |
| other keys | untouched |

`requirements_pipeline` = `record_version`, `component_versions` (pipeline, contract, record, extraction prompt, injection guard, split-OR guard, warning
adapter), `provenance` `{extraction_prompt {version, sha256}, model}`, `job_description_sha256`, `raw_response` `{text, sha256, finish_reason}`,
`raw_ai_output`, `extraction`, `original_digest`, `review_records` `{injection, split_or, model_warnings}`.
It holds no requirements document, no snapshot and no derived value. `original_analysis_json` is never written. Readiness, gates, unresolved issues,
normalized warnings and informational items are recomputed from (record, current document, original snapshot, admin policy) on every read and write.
`pipe.to_record(extract_result, model=...)` builds the record (for the future worker; nothing in production calls it).

Integrity (`verify_record`): record version, raw-response hash, record shape, job-description hash of each record, and `original_digest` against
`snapshot_original(original_analysis_json.requirements)`.

## API contract (routes unchanged)

* **GET** `/jobs/{id}/requirements` adds: `pipeline` `{status: ok|unavailable|invalid_record, available, errors, message, contract_version, record_version,
  component_versions, provenance, job_description_sha256, raw_response {sha256, finish_reason, bytes[, text]}[, raw_ai_output]}` (raw text and parsed output
  only for editors), `readiness.guarded`, `readiness.basis` (`pipeline` | `frozen_only` | `pipeline_record_invalid`), `readiness.by_policy`,
  `unresolved_issues`, `gates`, `normalized_warnings`, `informational`. Existing keys keep their shape.
* **PUT** `/requirements`: unchanged skeleton (role, tenant/job access, `FOR UPDATE`, `expected_revision`, retired ids, weight columns, strict audit in the
  same transaction). `validate_final` unchanged: invalid structure or weights -> **422, nothing written**. A valid document with unresolved blockers is
  **saved**; `readiness.can_proceed` stays false. The guard records are reconciled against the document being stored and written in the same UPDATE.
* **POST** `/classification-warnings/acknowledge` takes an optional `gate` (default `classification`): `classification` (frozen lifecycle) or `conflict`
  (pipeline lifecycle). `injection`, `split_or`, `structure`, `confirmation`, `validation`, `no_items`, `extraction` -> 409 `gate_not_acknowledgeable`;
  unknown -> 422 `unknown_gate`; `conflict` without a record -> 409 `pipeline_data_missing`.
* **POST** `/confirm-no-numeric-score`: with a usable record it additionally requires pipeline readiness `needs_confirmation` (else 409
  `nothing_to_confirm` with the pipeline state); without a record the frozen behaviour applies.
* **POST** `/structure-review/confirm`: unchanged logic; the record is validated first.
* Any body may echo `requirements_pipeline`, `pipeline`, `review_records`, `raw_response`, `raw_ai_output`, `component_versions`, `provenance`, `readiness`,
  `gates`, `unresolved_issues`, `normalized_warnings`, `informational`, `original`, `original_digest`, `extraction` (inside `requirements` or at the top level).
  They are never read and are listed in `discarded_client_fields` (`body.<name>` for top-level ones).
* A record that fails `verify_record` makes every write 409 `pipeline_record_invalid` (fail closed, nothing written); reads still return the requirements and
  the original with `readiness.state = pipeline_record_invalid`, `can_proceed = false`.
* Policy: the existing admin setting, read at request time. Classification and conflict review block only when acknowledgment is required; injection,
  split-OR, structure review and the preferred-only confirmation never depend on it.

New audit actions (same transaction): `requirements_conflict_acknowledged` and `requirements_<component>_<event>` for the guard/adapter events returned by the
reconcile (`requirements_injection_guard_resolved|reopened`, `requirements_split_or_guard_resolved|reopened`, `requirements_warning_adapter_resolved`,
`requirements_warning_adapter_acknowledgment_invalidated`).

## Compatibility: v2 records without pipeline data

No record is invented and nothing silently reports guarded readiness. GET answers `pipeline.status = unavailable` with an explanatory message and no
provenance, `readiness.guarded = false`, `readiness.basis = frozen_only` (the frozen readiness, exactly as before), and `unresolved_issues`, `gates`,
`normalized_warnings`, `informational` = **null** (an empty list would claim "no issues"). `original` stays available. Saves, classification
acknowledgment, structure and preferred-only confirmation behave as before and never create a record. Conflict acknowledgment answers 409
`pipeline_data_missing`. Whoever consumes `can_proceed` for such a record must treat `guarded = false` as "injection, split-OR and model-conflict checks not
applied".

## Coverage

`tests/test_requirements_pipeline_service.py` (no database): source/AST parity with the approved pipeline, import boundaries, all 48 stored responses through
a stored record, step-by-step edit-lifecycle parity, record integrity, view modes. `tests/test_requirements_pipeline_postgres.py` (real PostgreSQL via
`pgserver`; skipped without it): persistence, GET, compatibility, 422/no-write, forged client and stored state, acknowledgments, combined lifecycles under both
policies (simultaneous issues, partial correction, reversal, item removal), policy changes, concurrency, audit-failure rollback.
