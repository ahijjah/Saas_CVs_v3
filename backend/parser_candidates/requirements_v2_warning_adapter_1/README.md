# requirements-v2-warning-adapter-1 (candidate, offline)

Status: **candidate only.** Not imported by any API, worker, router or UI; no migration, no prompt or registry change. The frozen parser, prompts v2-1/v2-2,
the benchmark (scorer, labels, matching, gates) and the two other candidate guards are untouched. It lives outside `services/requirements_v2/` (pinned to
059c56b) and composes with `requirements_v2_injection_guard_1` and `requirements_v2_split_or_guard_1`.

## Why

The model reports ambiguities and conflicts in `warnings`. The frozen parser keeps only string warnings (`ai_warnings`) and silently drops objects such as
`{"text","reason","source_text"}` (3 of the 4 warnings in the stored v2-2 run), and nothing links a warning to an item. A correct "PostgreSQL is optional but
listed as required" warning therefore never reaches the recruiter workflow.

## Behaviour

**1. Normalization (`normalize_warnings`).** One record per warning, in order, nothing dropped:
`raw` (verbatim), `form` (`string` | `object` | `malformed`), `text` / `reason` / `source_text` (the supported object fields), `extra` (every other field, kept), and
`format_issues`: `unrecognized_field:<k>`, `field_not_string:<k>`, `unrecognized_warning_format` (an object with no usable text or reason), `unsupported_type:<T>`,
`empty_warning`. A `warnings` value that is not a list is treated as a single warning. Malformed or unrecognized warnings are preserved for inspection and are
**never interpreted**: their content is not scanned and they can never be linked or become a conflict.

**2. Classification and linkage (`build_review`).** Order: duplicate wording (`duplicate`, `تكرار`, "already listed") -> `duplicate`; no importance wording
(optional / required / preferred / mandatory / nice to have / `اختياري` / `مطلوب` / `يفضل` ...) -> `generic` (ambiguity or conflict talk without importance
wording) or `unrelated`. With importance wording the warning is matched to items: an item is *mentioned* when at least half of its content words occur in the
warning's text, reason or source_text (Latin runs are split from Arabic prefixes, so "وHTML" yields `html`), or the warning's `source_text` span overlaps the item's
evidence span. No exact quotation is required. A mentioned item is **linked** only if the job description itself states it with contradictory importance:
among its non-heading, non-AI-directed sentences that mention the item, at least one is a *required* statement and one a *preferred/optional* statement
(own wording first, else the governing heading: "Requirements:" vs "Nice to have:"). Result kinds:

| kind | meaning | blocks? |
|---|---|---|
| `importance_conflict` | importance wording + mentions item(s) + the JD contradicts itself about them | item-specific warning per item; policy decides |
| `importance_note_uncorroborated` | mentions an item but the JD is consistent about it (an ordinary optional note, or an unverified claim) | no |
| `importance_conflict_item_not_identified` | importance wording but no item could be identified | no |
| `duplicate`, `generic`, `unrelated`, `malformed` | informational | no |

Every informational warning carries a `limitation` sentence that says why it was not linked. Nothing is reclassified, restructured or reweighted by the adapter.

**3. Item-specific conflicts and the existing lifecycle.** Warning id `<item_id>:model_importance_conflict`, stored in a separate server-owned record with the evidence
(model warning text/reason/source, the JD statements) and its hash. The lifecycle mirrors the frozen classification-warning lifecycle and reuses its item state/hash:
* **active** while the item exists with the importance it had when flagged; **unresolved** until validly acknowledged;
* **policy Yes**: unresolved conflicts make readiness `needs_conflict_review`; **policy No**: visible and non-blocking;
* **correction**: reclassifying the item (the recruiter chose the other reading) makes the warning **inactive**; removing the item **resolves** it permanently;
* **acknowledgment** (`acknowledge`, trusted server code, roles as for classification warnings) is valid only while the item state (id, category, text, importance,
  source text, alternatives, experience) and the warning evidence still match; any change invalidates it, and `reconcile` prunes it with a reason
  (`item_or_evidence_changed`, `warning_inactive`, `warning_resolved`) for the audit log;
* **reversal**: the item back to the flagged importance reopens the warning; the old acknowledgment is never restored (a fresh one is required). As for the frozen
  warnings, `reconcile` must run on every save; a round trip that skipped it is the one case where the hash would match again.

**4. Composition (`readiness`).** invalid document / injection / split-OR first (mandatory under either policy; an acknowledgment of a conflict cannot reach them),
then the frozen classification review, then `needs_conflict_review` (policy Yes, unresolved conflicts), then the frozen result. Conflict reasons are appended to the
stronger states for visibility.

## Detection limits

* Linkage needs an item name (content words) in the warning, or an overlapping `source_text`, and a contradiction **visible in the JD**. A warning that describes the
  conflict without naming the item, or items whose text is generic (no content words), stay informational.
* Importance and contradiction wording is a vocabulary in English and Arabic; other languages and unusual phrasing are not recognized. "Contradiction wording" is only a
  flag (`explicit_conflict_wording`); the JD evidence decides.
* Statement classes come from the sentence wording or the nearest contiguous heading; a sentence with both optional and required words is left unclassified.
* A genuine model warning about a conflict that exists only across paragraphs without a shared item name is not linked. A false positive is cleared by
  reclassifying, removing, or acknowledging (policy Yes only).
* The adapter reads `res.raw_ai_output["warnings"]`; it cannot recover warnings the model never produced (v2-2 B06 run 1 had none, so PostgreSQL there is only caught
  by the frozen cue check).

## Replay (offline)

`scripts/requirements_v2_warning_adapter_replay.py` writes `benchmark_results/requirements_v2/warning_adapter_replay/REPORT.md`. Stored runs: 5 calls have model warnings
(1 v2-1, 4 v2-2); the frozen parser kept 2 of the 5 (it kept 1 of the 4 v2-2 ones: the 3 objects were lost); 3 calls get an item-specific conflict (v2-1 B06 run 1, v2-2 B12
runs 1 and 2); the 2 B08 objects are classified `duplicate`. No stored call changes state: each is already held by a stronger gate (injection / frozen classification review)
or runs under policy No. The official gates are recomputed by the frozen scorer from the same answers and are unaffected.

## Proposed API / UI integration (NOT implemented; for approval)

1. **Storage**: `job_criteria.analysis_json["requirements_warning_review"]`, written by the extraction worker after the frozen parser via `build_review(jd, raw_ai_output, doc)`;
   never accepted from a client (`carry_warning_review` on every PUT). Audit `requirements_model_warnings_recorded`.
2. **GET /jobs/{id}/requirements** adds `model_warnings` (`visible_warnings`: kind, normalized fields, extras, format issues, limitation), `conflict_warnings` (item warnings with status,
   evidence, acknowledged/unresolved) and returns `readiness` from `readiness(...)`. No other field changes.
3. **PUT /jobs/{id}/requirements**: in the locked transaction, after the existing reconciliations, call `reconcile(stored_warning_review, new_doc)`; persist; audit
   `requirements_conflict_ack_invalidated` / `requirements_conflict_resolved` from its events. Correction (reclassify / remove) uses the existing item edits.
4. **POST /jobs/{id}/requirements/conflict-warnings/acknowledge** `{expected_revision, warning_id}`: same roles, locking, revision check and audit as the existing classification
   acknowledgment; server-stamped user/time; calls `acknowledge`. The policy key `job_analysis.require_classification_acknowledgment` is reused (no new setting).
5. **UI (en/ar, RTL)**: a "Model notes" panel listing every warning (informational ones with their limitation text; malformed ones with a "format not recognized" badge and
   the raw content); on an affected item a badge with the model note and the two JD statements, plus **Keep as is (acknowledge)** (policy Yes) and the existing
   reclassify / delete controls. Under policy No the badge is shown without a button. No control touches the injection or split-OR blockers.
