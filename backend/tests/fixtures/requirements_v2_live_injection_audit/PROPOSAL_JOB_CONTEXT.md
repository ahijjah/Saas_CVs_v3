# Proposal: job-context injection gap (G3) and description-edit binding (G7)

Scope: a narrow proposal. It does NOT implement anything. It names the decisions that must come first, and which of them touch the approved pipeline.

## 1. Every field sent to the model

The model receives the job context lines (`services/requirements_v2/extraction/prompt.py`, `_CONTEXT_FIELDS`) and then the description. Only non-empty
fields are sent.

| Field sent | Source at creation | Stored column | Editable after extraction | Scanned by injection guard | In record hash |
|---|---|---|---|---|---|
| Job Title (`title`) | request body, unstripped | `jobs.title` | yes, `PUT /{job_id}`, admin or hr_manager, stripped | no | no |
| Department | request body | `jobs.department` | yes, same endpoint | no | no |
| Seniority Level (`experience_level`) | request body | `jobs.experience_level` | yes, same endpoint | no | no |
| Location | request body | `jobs.location` | yes, same endpoint | no | no |
| Employment Type (`job_type`) | request body | `jobs.job_type` | yes, same endpoint | no | no |
| Work Mode | request body | `jobs.work_mode` | yes, same endpoint | no | no |
| Description (the JD text) | request body, unstripped | `jobs.description` | yes, same endpoint, stripped, no re-extraction | yes, the description only | yes, `job_description_sha256` |

Also sent to the model, unscanned: nothing else. The system prompt is fixed.

## 2. Provenance and threat model

- Creation: a job creator submits all fields. Editing: admin or hr_manager. There is no third-party ingestion of these fields in the v2 path.
- The description has been the threat model since the B06 work. The same reasoning applies to the title and other fields, because they are free text
  that the model reads as part of the same request. A pasted title is as plausible as a pasted JD.
- Therefore a directive can enter through any of the six fields, at creation or by a later edit.

## 3. Hash binding

Today only the description is bound (`job_description_sha256`). Nothing binds the context fields, so a later title edit cannot be told apart from the
text the model saw.

Proposal: record `job_context_sha256` = SHA-256 of the canonical JSON of the six fields exactly as they were sent, taken at the moment the worker builds
the prompt. Consequences:
- After extraction, a difference between the stored context and the current one is detectable, for both the description and the context.
- The coverage check and any future staleness check can use the same mechanism.

This changes the record contract (new key, record version bump, `verify_record` change). See decision D1.

## 4. Detection

Proposal: run the existing `instruction_spans` rule set (no new rules) on each non-empty context field, in the live path only, before the model call or
before the draft is stored. The rule set is unchanged; only its input is widened.

Dispositions, in order of preference:

- **O1 (recommended, no approved-pipeline change).** In `services/requirements_v2_extraction.analyse` (live path, outside the approved package, and
  already allowed to import the pipeline package): if any context field has an instruction span, the attempt fails with code
  `job_context_instruction` and a message naming the field. No draft is stored. The recruiter edits the field and uses the existing
  `request_extraction_retry`. This contradicts the current stated rule that a blocker is never an extraction failure, so it needs decision D2.
- **O2 (needs D3).** Store the draft with an open review for the context, as the description already is. This needs a new issue kind in the approved
  injection schema and readiness reasons from the approved pipeline.

## 5. Post-edit handling

- **Context edits after extraction.** With D1 in place, the stored hash no longer matches. Proposal: show a non-blocking warning, as coverage does today,
  and let the recruiter re-extract. Whether a changed context should BLOCK readiness is decision D4.
- **Edits that introduce an instruction.** The edit endpoint should run the same detection and refuse the edit with a 422 naming the field. This needs
  `routers/jobs.py` to reach the scanner. The boundary test allows only the API layer and its router to reference the pipeline package, so this is
  decision D5.
- **Description edits (G7).** Highest priority. A changed description hash should BLOCK the scoring guard and readiness until re-extraction. The guard
  would load the current description (one more column in `ensure_job_evaluable`'s query) and compare hashes. This needs no approved-pipeline change for
  the guard. The readiness display needs decision D6 (see below), because readiness is computed in the approved pipeline.

## 6. Approved-pipeline boundary: what needs a separate decision before implementation

| Decision | Change | Why it crosses the boundary |
|---|---|---|
| D1 | Add `job_context_sha256` to the record; bump record version; extend `verify_record` | Approved `services/requirements_pipeline/core.py` and its byte-parity tests |
| D2 | Make a context instruction an extraction FAILURE (O1), not a draft with blockers | Reverses the stated rule in `services/requirements_pipeline` and the worker docstring. A policy change, not a code boundary |
| D3 | A new `context` issue kind in the injection review, and its readiness reason (O2) | Approved `services/requirements_pipeline/injection.py` schema, `validate_review`, and the readiness gates |
| D4 | Whether a changed context blocks readiness or only warns | Readiness rule in the approved pipeline |
| D5 | Let `routers/jobs.py` run the scanner on edits | Boundary allowlist in `tests/test_requirements_pipeline_service.py`; moving or copying the scanner touches the approved source |
| D6 | Readiness reason for "description changed since extraction" | Readiness is computed in the approved pipeline (`compute_readiness`/adapter) |

Not needing an approved-pipeline change: O1 itself (in `services/requirements_v2_extraction.py`), the guard's description-hash check (in
`services/requirements_guard.py`, which must not import the pipeline package), and the description-edit warning in the API layer.

## 7. Frozen artifacts and policy

- No frozen path is touched: `services/requirements_v2`, the benchmark fixtures, the frozen scorer, the prompts, historical runs and review packages.
- The existing injection-review policy stays as it is: an injection issue is a review item, not a silent drop, and a readiness blocker is not an
  extraction failure, except where D2 is taken explicitly.

## 8. Tests to write with the implementation (not written now)

- A context field with an instruction span: O1 fails the attempt with `job_context_instruction`; no record is stored.
- A clean context and a clean description: no change in behaviour (regression against the stored answers).
- A changed description after extraction: the guard refuses with the description reason, under the switch on.
- A changed title after extraction: the hash mismatch is reported; readiness and scoring follow D4.
- The boundary test updated only if D5 is decided.
- Replays of the 48 stored responses: unchanged results.

## 9. Recommended order

1. G7 first: the description-edit binding (guard hash check, API warning). It needs no approved change beyond D6, and it closes an active hole.
2. O1 with D2: context scanning as an extraction failure. No approved-pipeline change.
3. D1: the context hash binding, if the record contract change is accepted.
4. D5: the edit refusal, only after the scanner can be shared without a boundary change.

## 10. Decisions needed from you

- D1: accept a record contract change (`job_context_sha256`), or keep binding to the description only.
- D2: accept context instructions as extraction failures (O1), or require a review item (O2 with D3).
- D3: only if O2 is preferred.
- D4: should a changed title or context block readiness, or only warn?
- D5: may the scanner be reached from the jobs router (allowlist change), or should edits stay unscanned?
- D6: accept a readiness reason for a changed description (approved pipeline change), or handle it only in the guard?
