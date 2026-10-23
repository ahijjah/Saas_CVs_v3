# Gates / Knockout Criteria

One eligibility model, built on the Requirements v2 document and the existing knockout questions. There is no second knockout system.

## Concepts

| Concept | Where it lives | Enforces anything? |
| --- | --- | --- |
| **AI suggested gate** | `gate_suggestion` on a Required item of `job_criteria.analysis_json["requirements"]`. Written only by extraction, from the job description's own explicit wording (must, mandatory, required, minimum, essential, valid licence, legally required and the Arabic equivalents). It keeps the cue and the source text for comparison and audit. | No |
| **Mandatory Gate** | `gate` on a Required item: the requirement is designated mandatory (verified from CV evidence in the UI; older records may name another verification method), plus the confirming user and time stamped by the server. There is no failure action to choose. | Yes, once saved by an Admin or HR Manager |
| **Eligibility-gate question** | `job_knockout_questions.is_eligibility_gate` (the old `gate_failure_action` column is legacy and unused). A question is advisory unless this is switched on explicitly. | Yes, when on |
| **Response Required** | The former "Required" label of a question (`is_required`): the candidate must answer to submit. Same data meaning, not eligibility. | No |

Only Required items can be gates. A Preferred item cannot, and an item that becomes Preferred loses its gate. A gate item stays in normal required-item scoring: the score formula is untouched.

## Decision

A gate has no failure action to configure. Its outcome follows the evidence (`services/eligibility_service.decide`; the score is not an input):

1. Every enforced gate clearly satisfied (or overridden by a person) → **Eligible**, normal flow continues.
2. Clear, affirmative, reliable evidence that a gate is not satisfied (result `failed`) → **Not Eligible**, whatever the score.
3. Evidence missing, unclear, conflicting, incomplete or unverifiable (`needs_verification`), or not assessed yet (`not_assessed`) → **Review Required**. Missing evidence never makes a candidate Not Eligible.
4. A technical failure (the model call, unreadable or incomplete model output) is a **processing error**, not a result about the candidate. An earlier decisive result stands; otherwise the gate stays `not_assessed` with a "Processing error" note (`processing_error: true` in the Gate Summary) and the candidate is Review Required until "Check again" retries it. It is never Not Eligible.
5. A job without enforced gates → `not_applicable`.

`applications.decision` (the score's decision) is never rewritten. `effective_decision` is derived: Not Eligible or Review Required replaces the score outcome on screen, and the numeric score stays visible with a note that it cannot override a failed gate.

The CV check is one model call per application. A gate **passes** only on a quote found verbatim in the CV text at confidence ≥ 0.75, and **fails** only on a quoted explicit contradiction at confidence ≥ 0.90; every other verdict (including "conflicting") is `needs_verification`. Question gates are judged from the candidate's own answer; no answer, or an answer that cannot be read against the criteria, is `needs_verification`. Assessment and manual gates wait as `not_assessed` until a result is recorded. Knockout Questions remain a separate candidate-answer mechanism.

### Legacy failure action
Earlier versions stored a `failure_action` (`not_eligible` / `hold_for_review`) on requirement gates, on `job_knockout_questions.gate_failure_action` and on `application_gate_results.failure_action`. These values stay readable and valid, are **never read for a decision**, are not returned by the API and are not shown in the UI. `set_gate` and the question save still accept a valid `failure_action` from a stale client and ignore it (a malformed one is refused); an existing stored value is left as it was, and new records get the canonical value so older code can still read them. No migration was needed.

## Overrides

Admin and HR Manager only, with a required reason. Stored: the computed result, the effective result `overridden`, the user, the time and the reason, plus an append-only event (`application_gate_result_events`) with original result, new result, user and time. An override can be withdrawn; the event is kept. Recruiters and Viewers can read the Gate Summary only.

## Assessment results later

`POST /applications/{id}/gates/{gate_key}/result` with `{"result": "passed|failed|needs_verification", "summary": "…"}` records the result of an `assessment` or `manual` gate. It is the API an assessment integration will call; no external integration exists yet.

## API

| Route | Roles |
| --- | --- |
| `GET /applications/{id}/gates` | every tenant role |
| `POST /applications/{id}/gates/evaluate` | admin, hr_manager |
| `POST /applications/{id}/gates/{gate_key}/result` | admin, hr_manager |
| `POST /applications/{id}/gates/{gate_key}/override` | admin, hr_manager |
| `DELETE /applications/{id}/gates/{gate_key}/override` | admin, hr_manager |

Requirement gates are confirmed with the existing `POST /jobs/{id}/requirements/edits` operations `set_gate` and `clear_gate` (admin, hr_manager). The knockout-question save accepts `question_id`, `is_eligibility_gate` and `gate_failure_action` per question.

## Rules checked when a Job Analysis is saved

A gate on a Preferred item; a gate without a verification method; a `candidate_question` gate without a linked question; a linked question with another method; one question linked to two requirements; a linked question that does not exist (another tenant's question is reported the same way), belongs to another job, has no valid passing criteria, or is not itself an eligibility gate. A question that a requirement gate is linked to cannot be removed or stop being a gate until the gate is cleared or re-linked.

## Database (migration 102)

Additive and idempotent. `job_knockout_questions.is_eligibility_gate` defaults to **false**, so every existing question stays advisory. `applications.eligibility_status` defaults to `not_applicable`. New tables `application_gate_results` and `application_gate_result_events` carry row-level security (tenant policy plus the worker policy) and are covered by the runtime privilege matrix.

## Rollback

`db/rollback/102_gates_knockout_criteria.down.sql` removes exactly what 102 added. It is not a migration and the runner never reads it. Run it on a restored copy first, and only together with deploying code that predates 102. It loses gate results and their history and the question gate settings; applications, scores, decisions, questions, answers and requirements documents are untouched. Confirmed gates and suggestions inside requirements documents stay in the document; the older validator refuses unknown item keys, so a job carrying them cannot be edited by older code until they are removed (the optional strip statement at the end of the script does that). The normal rollback remains restoring the pre-upgrade backup.
