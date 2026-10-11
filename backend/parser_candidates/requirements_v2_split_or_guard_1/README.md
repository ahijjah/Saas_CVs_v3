# requirements-v2-split-or-guard-1 (candidate, offline)

Status: **candidate only.** Not imported by any API, worker, router or UI; no migration, no prompt or registry change. The frozen parser, prompt v2-1,
the v2-2 candidate prompt and the benchmark (scorer, labels, matching, gates) are untouched and asserted unchanged by the tests. It lives outside
`services/requirements_v2/`, which the benchmark executor pins byte-for-byte to commit 059c56b. It composes with
`requirements_v2_injection_guard_1` (injection always wins).

## The defect it guards against

"Python or Java" is one requirement with two accepted options. The model sometimes returns **two items sharing the same source sentence**, one per
option, each pointing at the other. Two observed forms:

| form | example | what the frozen parser does |
|---|---|---|
| `single_entry_mutual` (v2-1) | `Python` / `["Java"]`, `Java` / `["Python"]` | drops both one-entry lists (`alternatives_invalid_dropped`): the job then shows two independent required items (an AND) |
| `complete_alternatives_repeated` (v2-2) | `Python` / `["Python","Java"]`, `Java` / `["Python","Java"]` | valid, silent: one requirement with two weight slots |

## Detection (raw extraction, before anything is dropped)

Input: the raw AI output (`result.raw_ai_output`) and the draft document. Raw items are paired with document items category by category in order
(the same usability filter as the parser; a category that does not line up is skipped, i.e. no finding). A group is flagged when **all** hold:

1. same category;
2. the same normalized source evidence (the document's JD slice, else the raw quote), shared by 2+ items;
3. that evidence contains an **OR connective**: `or`, `either`, `x/y`, `أو`/`او` (this is what keeps "Excel and Power BI" / "إجادة Excel وPower BI" apart);
4. the items' **raw** alternatives link them: every member's alternatives name another member's text, **or** one member's alternatives name all the
   others (covers complete, partial and three-way forms). An option matches by normalized equality (any length, so "R" counts) or by word containment
   of at least 3 characters ("اللغة العربية" / "العربية").

Not flagged: independent items sharing an AND sentence (with or without alternatives), an OR sentence whose items carry no alternatives, the correct
single OR item, repeated requirements with different evidence, the same evidence in different categories, unrelated items. Ordinary duplicate/similarity
warnings stay informational.

Each issue records: category, item ids and texts, form, options, shared evidence, the raw alternatives, the document alternatives at detection, rule
codes and a reason.

## Effect and resolution

* The review record is separate and server-owned. All items stay visible; nothing is merged, deleted, reclassified or reweighted (the double weight
  slot remains visible and is left to the recruiter). The raw AI output and the original snapshot are never touched.
* `composed_readiness()`: injection issue open -> `needs_injection_review` (open split-OR reasons are listed too); else split-OR issue open ->
  **`needs_split_or_review`**; else the frozen result. Under **both** classification-policy values; an invalid document keeps `needs_review`.
  Acknowledgment cannot bypass it. Fixing the OR issue can never clear an injection blocker, and vice versa.
* Openness is **derived from the current document** on every call (a stored or client-sent status cannot close it):
  * all group items removed -> resolved `group_removed` (normal validation still applies: an otherwise empty job is `needs_items`);
  * exactly one group item remains **and its current alternatives cover every option** (>= 2 entries) -> resolved
    `kept_one_item_with_full_alternatives`. A kept item without them stays open (otherwise an option would silently disappear);
  * two or more remain -> still open while they still link each other, using the alternatives the recruiter has *changed* (`set_structure`) or, if she
    has not touched them, the raw ones; if her edits leave nothing linking them -> resolved `decoupled_by_recruiter` (the false-positive path for the
    complete form: clear the alternatives of every item that lists the others; one remaining list is enough to keep it open).
* The existing editing operations suffice: `remove_item` (redundant item), `set_structure(alternatives=[...])` and `set_text` (v2-1 form), then the
  frozen weight tools (`equalize_category` / `set_item_weight`) because the frozen validation requires the item weights of a category to total 100
  after a removal. Nothing is rebalanced for her.
* An unrelated edit (other items, other categories, weights, the group items' unrelated text) never closes it. Restoring the split (the removed items
  are back, e.g. an older version is saved) or removing the kept item's full alternatives reopens it. `reconcile(review, doc, user_id, at)` returns the
  updated record plus resolved/reopened events for the audit log; idempotent.

## Limits

* A group needs two or more option items. A single item that lost an option (v2-1 B02 run 1: only "Python" with `["R"]`) is a different defect and is not
  flagged.
* Needs an OR connective in the quoted evidence and alternatives that link the items. Options split without any alternatives, evidence without a
  connective, or a connective in another language are not detected. Raw/document pairing failures yield no finding.
* v2-1 form false positives cannot be decoupled by clearing lists (the frozen parser already dropped them, so the edit is invisible): remove both
  items and add them as the recruiter's own items (`recruiter_added`, never in a group), which resolves as `group_removed`.
* A recruiter who adds a new item for the removed option creates a new, untracked item; that is her decision.
* It does not judge whether the kept item's wording is good, only that its alternatives cover the options.

## Replay (offline)

`scripts/requirements_v2_split_or_guard_replay.py` replays the stored v2-1 and v2-2 runs and writes
`benchmark_results/requirements_v2/split_or_guard_replay/REPORT.md`: 7 v2-1 calls and 4 v2-2 calls have a split group; readiness changes are reported
separately from the official gates, which are recomputed by the frozen scorer from the same answers and are unaffected.

## Proposed API / UI design (NOT implemented; for approval)

The existing editing operations already resolve everything, so no new resolution endpoint is needed:

1. **Storage**: `job_criteria.analysis_json["requirements_split_or_review"]`, written by the extraction worker after the frozen parser via
   `inspect_result`; never accepted from the client (`carry_split_or_review` on every PUT). Audit `requirements_split_or_detected`.
2. **GET /jobs/{id}/requirements** adds `split_or_review` (issues, status, options, evidence, form) and returns `composed_readiness`.
3. **PUT /jobs/{id}/requirements**: in the existing locked transaction, after `validate_final`, call `reconcile(stored, new_doc, user, now)` and persist; audit
   `requirements_split_or_resolved` / `requirements_split_or_reopened`. Saving is never blocked by an open issue (it is how it is resolved); every
   "proceed" action uses `composed_readiness`.
4. **UI (en/ar, RTL)**: a blocking banner "These items look like one OR requirement ("Python or Java") split into one item per option." Both items are shown
   together with the shared quotation. Buttons: **Merge into one item** (a convenience that performs, as ordinary edits in one save: set the kept item's
   text and alternatives, delete the other; nothing happens automatically), **Keep both as separate requirements** (clears the links / removes and re-adds as
   own items, recorded in the audit trail) and **Delete both**. No dismiss/acknowledge control. Weights are shown but not changed; after a merge the
   category's item weights are rebalanced only when the recruiter chooses "Equalize".
