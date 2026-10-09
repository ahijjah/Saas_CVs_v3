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

`python scripts/requirements_v2_pipeline_replay.py --out benchmark_results/requirements_v2/pipeline_replay` → `REPORT.md`, `report.json`. Readiness and unresolved issues per call and policy are reported separately from the official gates, which are recomputed by the frozen scorer and compared with the saved `results.json` (identical). The pipeline's readiness is not a benchmark score.

## Integration steps (not done)

1. **Storage**: persist the state (JSON) next to the existing requirements-v2 record, or split: document + `review_records` + `raw_response` + `original`. `readiness`, `gates`, `unresolved_issues`, `normalized_warnings`, `informational` need not be stored (recomputed); if stored they are display caches. No migration is needed if the existing JSON column holds the state (decision below).
2. **Worker**: after the model call, feed the raw text and finish_reason to `extract(...)` instead of `parse_response`; record the real prompt version/sha in `extraction_prompt`; persist the state.
3. **GET**: return the state with `evaluate(state, policy=<admin setting read at request time>)` so a policy change takes effect without re-extraction.
4. **PUT / save**: authenticate and authorize, load the stored state, call `reconcile(stored, body["requirements"], user_id, now, require_classification_acknowledgment=<admin setting>)`, ignore every other field of the body, persist the new state, append the returned events to the audit log. Reject the save only for transport errors; an unblocked save is whatever `readiness` says.
5. **Acknowledge endpoint**: `POST .../acknowledge {gate: "classification"|"conflict", warning_id}` → `acknowledge(...)`; map `PipelineError.code` to 409/422.
6. **Structure / confirmation endpoints**: `confirm_structure`, `confirm_no_numeric_score` (409 on `not_ready_for_confirmation`).
7. **Proceed gate**: wherever a requirements-v2 record may be used for scoring, require `evaluate(state)["readiness"]["can_proceed"]` for the current policy.
8. **UI**: show `unresolved_issues` grouped by gate in precedence order with `resolution_options`; injection and split-OR issues have no acknowledge button (edit or remove the item; correct the category weight); classification / conflict show an acknowledge action only when `blocks_when_ack_required`; list `normalized_warnings` and `informational` (similarity, generic notes) as read-only; show `raw_response` / `original` for comparison.
9. **Tests to add at that point**: the API-level equivalents of `tests/test_requirements_v2_pipeline.py` (forged body, stale body, role checks, policy flip).

## Unresolved decisions

* Where the state lives (existing column vs. new storage → possible migration) and the role allowed to acknowledge.
* Whether injection / split-OR false positives need an escape path (today a recruiter must edit or remove the item; there is deliberately no acknowledgment).
* Position of the conflict gate (currently after the frozen classification review, both policy-governed).
* The frozen parser pins prompt v2-1 for its own bookkeeping; the real prompt is recorded by the caller. Prompt v2-2 is not activated or registered, and there is no held-out benchmark set yet for either the prompt or the guards (all detection rules were developed against the 12 benchmark JDs).
* Performance/size: the state is about 25 KB per extraction; the raw text is stored in full.
* Retention of `raw_response` (may contain personal data if real JDs contain any).
