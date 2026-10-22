# Gates / Knockout Criteria

One eligibility model, built on the Requirements v2 document and the existing knockout questions. There is no second knockout system.

## Concepts

| Concept | Where it lives | Enforces anything? |
| --- | --- | --- |
| **AI suggested gate** | `gate_suggestion` on a Required item of `job_criteria.analysis_json["requirements"]`. Written only by extraction, from the job description's own explicit wording (must, mandatory, required, minimum, essential, valid licence, legally required and the Arabic equivalents). It keeps the cue and the source text for comparison and audit. | No |
| **Mandatory Gate** | `gate` on a Required item: verification method (`cv_evidence`, `candidate_question`, `assessment`, `manual`), failure action (`not_eligible`, `hold_for_review`), optional `linked_question_id`, and the confirming user and time stamped by the server. | Yes, once saved by an Admin or HR Manager |
| **Eligibility-gate question** | `job_knockout_questions.is_eligibility_gate` + `gate_failure_action`. A question is advisory unless this is switched on explicitly. | Yes, when on |
| **Response Required** | The former "Required" label of a question (`is_required`): the candidate must answer to submit. Same data meaning, not eligibility. | No |

Only Required items can be gates. A Preferred item cannot, and an item that becomes Preferred loses its gate. A gate item stays in normal required-item scoring: the score formula is untouched.

## Decision

Per application, from the effective result of each enforced gate (`services/eligibility_service.decide`; the score is not an input):

1. any `failed` gate whose action is `not_eligible` → **Not Eligible**, whatever the score;
2. otherwise any `failed` (hold for review), `needs_verification` or `not_assessed` gate → **Review Required**;
3. otherwise (all `passed` or `overridden`) → **Eligible** and the normal flow continues;
4. a job without enforced gates → `not_applicable`.

`applications.decision` (the score's decision) is never rewritten. `effective_decision` is derived: a failed gate or an unresolved one replaces the score outcome on screen, and the numeric score stays visible with a note that it cannot override a failed gate.

Missing, ambiguous or low-confidence CV evidence never fails a gate. The CV check is one model call per application; a gate **passes** only on a quote found verbatim in the CV text at confidence ≥ 0.75, and **fails** only on a quoted explicit contradiction at confidence ≥ 0.90. Everything else, including a failed call, is `needs_verification`. Question gates are judged from the candidate's own answer to the linked question; no answer (for example an email-intake application) is `needs_verification`. Assessment and manual gates wait as `not_assessed` until a result is recorded.

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
