# Implementation note: model-input binding, context scanning and in-flight checks

Scope: the six decisions in the last instruction. Scoring stays disabled. Nothing was deployed, activated or re-extracted. No paid call was made.

## What is bound, and how

- `services/requirements_model_inputs.py` (new, pure, no pipeline import): the exact description and the six context fields the model receives,
  one SHA-256 per field, and one SHA-256 over all of them. An empty or missing field is bound as absent, which matches what `build_user_message`
  sends. A test checks that a change in the binding happens exactly when the prompt text changes.
- The analysis is bound at the moment the model's response is analysed (`services/requirements_v2_extraction.analyse`, which now takes the job
  context). The binding records what the model was given. It is never recomputed to match later inputs.
- Record contract, v2 (`services/requirements_pipeline/core.py`): `RECORD_VERSION_V2` adds `model_inputs` (the binding) and `context_review`. The v1
  record and its verifier are unchanged; a v1 record still verifies, but its context is reported unverified. The pinned derived-view functions are
  untouched (their AST tests pass).

## Context scanning

- `scan_context` runs the approved instruction scanner on each non-empty context field. Each finding is an open `context_instruction` issue that keeps
  the exact snippet and its position. The draft is kept with blockers. A directive is never an extraction failure.
- The scanner is called only from `services/requirements_v2_extraction.py`, which was already an approved importer. The router allowlist is unchanged,
  and `routers/jobs.py` is unmodified.

## Edits after analysis

- Reads never rebind. `requirements_model_inputs.input_blockers` compares the stored binding with the job's current description and fields and
  reports each change: `description_changed` or `context_changed` (naming the field). A context directive gives `context_instruction`.
- The readiness view (`services/requirements_api.build_view`) applies these blockers over the pipeline readiness. The overlay can only restrict:
  `inputs_changed`, `inputs_unverified`, or `needs_injection_review`, with `can_proceed` false and no scoring mode. The draft and the original snapshot
  are not modified.
- The entry guard (`services/requirements_guard.assert_job_evaluable`) applies the same blockers when the scoring switch is on. It fails closed when
  the caller does not supply the job's current inputs. `ensure_job_evaluable` loads them. `cv_score` supplies them from its criteria query.

## In-flight changes

- `services/requirements_v2_extraction.finish_success` reads the job's description and context fields under a share lock (`FOR SHARE`) and compares
  them with the binding before writing anything. A change returns `inputs_changed`, and nothing is written. The worker records a failed attempt with
  code `inputs_changed_during_call`. Nothing is re-extracted automatically. The lock keeps a concurrent job edit waiting until this transaction ends.

## Older records

- A record with no binding is never claimed as verified. Its description is still checked. Its context is reported `context_unverified`, which blocks
  progression. It is never upgraded or rebound by reading it.

## Verified offline (no database)

- `tests/test_requirements_v2_input_binding.py` (43): the binding's coverage and its agreement with the prompt; unchanged inputs; a change to the
  description and to each context field; the in-flight decision (pure); directives in each context field; a directive keeps the draft with blockers;
  older records; unreadable inputs; a malformed binding; v2 and v1 record verification and tampering; the overlay is never more permissive; the guard
  with the switch on (clean unchanged passes, changed description or field refused, older record refused, missing inputs refused, directive refused,
  switch off refuses everything).
- `tests/test_requirements_v2_live_injection_gate.py` now binds v2 records and passes current inputs. Its pinned gap (title not scanned) is replaced
  by a test that the title is scanned.
- `tests/test_p001_scoring_path.py`: the fake criteria row gains the five context keys the scoring query now selects (no behavioural change).
- The approved-pipeline source-parity and boundary tests pass unchanged, which shows `injection.py`, `split_or.py`, `warning_adapter.py`, `text.py`
  and the allowlist are intact.
- Broad related set: 1113 passed, 16 skipped (the skipped ones need PostgreSQL or `pgserver`).

## NOT verified (database transaction and SQL paths)

These need PostgreSQL (`pgserver` is not declared in `requirements.txt` and is not installable here), so they did not run:
1. `finish_success`: the `FOR SHARE` read in `current_inputs`, the `inputs_changed` return, and the audit row it writes.
2. The worker's handling of `inputs_changed` (the failed attempt written by `finish_failure`, and the commit).
3. The extended loader in `requirements_api._load` (seven columns from `jobs`).
4. `requirements_guard.load_job_inputs` and the extended criteria query in `workers/cv_score.py`.
5. The existing isolated Postgres worker test for the B06 draft, which expects the v1 record shape in places; it needs review once it can run.
6. Whether a concurrent `PUT /{job_id}` really waits for the share lock (a PostgreSQL behaviour, not tested here).

## Open decisions and limitations

- No recovery path exists for a stale analysis. A job with a draft cannot be re-extracted (`request_extraction_retry` allows only jobs without a
  document). So a description or context change after analysis, or a directive present at creation, blocks progression until the bound inputs are
  restored. A reviewed re-analysis flow that keeps the draft and the original is needed. Decision required.
- Older (v1) analyses are blocked for context and are never upgraded. If any exist in production, a decision is needed on how they are handled.
- A context edit that adds a directive to an already analysed job is reported as a generic `context_changed`, not as a directive. The recruiter sees
  the field name, not the snippet. This is conservative and is recorded here rather than changed.
- Two new readiness states, `inputs_changed` and `inputs_unverified`, are emitted. Any UI that maps readiness states must handle them. Not checked
  here.
- Scoring remains disabled by `REQUIREMENTS_V2_SCORING_SUPPORTED = False`. The VPS containers run older code without any of this (see
  `AUDIT_LIVE_PATH.md`). None of it is deployed.
