# Final Candidate Matrix

A job-specific longlist where every application is one row and the decision elements sit side by side: **eligibility, overall score, mandatory criteria, preferred criteria, gates and review flags**. The overall score is one decision-support element. The matrix never builds a combined rank: ordering is the recruiter's explicit, single-column choice.

Open it from **Job Details → Candidate Matrix** (`/jobs/{job_id}/matrix`). Clicking a name opens the existing candidate details; clicking a summary, an individual result or a flag opens the evidence drawer (quotation, confidence, explanation, human resolution, gate evidence) over the stored item evaluations and gate results.

## Columns (stable ids)

| Group | Id | Notes |
|---|---|---|
| Candidate | `candidate.name` | always first, frozen by default, cannot be hidden |
| | `candidate.applied_at`, `candidate.source`, `candidate.assigned_to`, `candidate.tags` | |
| | `candidate.position`, `candidate.location` | **registered but unavailable**: no candidate position or location is stored anywhere, and the system does not infer them from the CV |
| Decision and score | `decision.eligibility`, `decision.score`, `decision.scoring`, `decision.flags` | |
| Mandatory | `mandatory.summary`; dynamic `mandatory:<requirement item id>` | one column per Required item |
| Preferred | `preferred.summary`; dynamic `preferred:<requirement item id>` | one column per Preferred item |
| Gates | `gates.summary`; dynamic `gate:<gate key>` (`req:<item id>` or `q:<question id>`) | one column per enforced gate |
| Workflow | `workflow.status` | |

Dynamic columns use the immutable requirement item id or gate key, never a label. A saved layout that names a column the job no longer has (a removed or superseded requirement) stays stored and readable; the API reports it as `unavailable_columns` and it is neither queried nor exported.

**Age** is not offered: no age or date-of-birth field is stored for candidates, and inferring it from a CV would be neither reliable nor appropriate. It would only be added from an explicit, legitimately stored field visible to a permitted role.

## What each summary means

All numbers come from the **current** scoring run (`application_scoring_runs.is_current`), its **effective** item results (a human resolution replaces the model's result) and the **effective** gate results (a human override counts as passed, with an override marker). They are aggregated in SQL; the frontend only names them.

* **Eligibility** (`eligible`, `not_eligible`, `review_required`, `not_assessed`): a confirmed failed gate is *Not Eligible* whatever the score is. Missing, unclear, conflicting or unverifiable evidence (a gate needing verification, or an unresolved Required item) is *Review Required*, never *Not Eligible*. A clean candidate with enforced gates is *Eligible*. With no enforced gate and nothing unresolved the status is *Not Assessed*. Gate outcomes never change the score.
* **Mandatory** — Met / Partially Met / Not Met / Unclear / Total. *Unclear* stays visibly flagged for a person and is never converted into a score or a result.
* **Preferred** — Yes / No / Unclear / Total (and *Not Assessed* per item). Preferred items cannot be partially met: the evaluator refuses `partially_met` for a Preferred item and the database constraint rejects it. The stored `not_clear` is shown as *Unclear*; any unexpected value would also be shown as *Unclear*, never as partial compliance.
* **Gates** — Passed / Failed / Review Required / Not Assessed / Total.
* **Flags** — counts per kind with a severity label, each linking to its targets: *unclear criteria*, *conflicting evidence*, *evidence not found in the CV*, *low confidence*, *processing error*, *unresolved review*. Severity says how directly a flag can change the outcome (gate-linked or processing error: high; a Required item: medium; otherwise low). There is **no AI risk score**.
  * The reasons come from `application_item_evaluations.review_reason` (migration 104): `no_verified_evidence` and `low_confidence` are set when the evaluator downgrades a result; `conflicting_evidence` only when the evaluator marks the item (`evidence_conflict`) and the result is unclear. An unmarked conflict still appears as *Unclear*. Rows scored before 104 were labelled once from the evaluator's own downgrade note.
* **Scoring status / method** — scored, review required, not scored, legacy score, none; item-level or legacy category; a stale marker when the requirements changed after the score.

### Legacy and unscored applications

An application without item-level results never breaks the matrix: its item summaries show *Not Assessed*, its legacy score stays readable and is labelled *Legacy score*, and applications not scored yet show no score.

## Configuration and precedence

A layout is `{columns (visibility and order), frozen_candidate, sort, filters, page_size}`, validated against the column registry for the job; unknown fields and unknown ids are refused.

Precedence, whole layout, first found wins: **personal view → job configuration → tenant default → system default**. "Reset" deletes a layer (personal, or the job's shared layout), which falls back to the next one. The tenant default may use only standard columns (criteria are per job). Writes carry `expected_version`; a stale write is a 409 conflict, never a silent overwrite. Unreadable stored layouts are skipped.

## Permissions

| Action | Roles |
|---|---|
| Read columns, config, rows | admin, HR manager, recruiter, viewer |
| Save/reset the tenant default or a job's shared layout | admin, HR manager (audited: `matrix_config_saved`, `matrix_config_reset`) |
| Save/reset one's personal view | admin, HR manager, recruiter |
| Export CSV | admin, HR manager, recruiter (the existing export roles; a viewer cannot export; audited: `matrix_exported`) |

A viewer can arrange columns for the session but cannot save or export. Every route is tenant-scoped (row-level security plus explicit tenant predicates, tested independently) and uses the Candidates workspace's client-assignment access rule; another tenant's job, layout or id is the same 404 as a missing one.

## Sorting, filtering, paging

Sortable: candidate name, application date, eligibility, score (missing scores last), scoring status, flags, mandatory (Met count), preferred (Yes count), gates (Failed count), workflow, and any individual criteria or gate column. One column at a time; ties break by newest application then application id, so paging is stable. Filters: search, eligibility, score range, scoring status, workflow status, source, flags, application dates, assignee, and per-criteria / per-gate results. Location cannot be filtered (not stored).

## Export

`POST /candidate-matrix/jobs/{id}/export` returns UTF-8 CSV (with a byte-order mark so spreadsheets read Arabic correctly) of exactly the visible columns, in order, for the current filters and sort, with words (English or Arabic) for statuses. Evidence quotations, confidence values and CV content are never columns. Cells starting with `= + - @` are neutralised against spreadsheet formulas. A metadata block lists the job, the **client organization and organization names** (the only existing branding sources), the filters, the sort and a note that the score is not a ranking. Limit: 5,000 rows (narrow the filters otherwise).

**Branding.** No tenant logo or theme store exists (only `branding_level` and a client organization `logo_url`). The matrix reuses the existing names and adds no HTML/CSS injection or new branding system.

## Performance

A page costs a fixed number of statements regardless of page size or column count: the job lookup, the registry (document + gates), the saved layers, one base query with SQL aggregates (current runs, item evaluations and gate results grouped once), then one query each for tags, visible criteria cells, gate results and flagged evaluations. No per-row queries.

## Migration and rollback

`104_candidate_matrix.sql` (additive, idempotent, owned by `cv_migrator`): `candidate_matrix_configs` (tenant / job / personal scope, RLS, versioned, 32 KB JSON cap) and `application_item_evaluations.review_reason` (+ one-time labelling and an index). Runtime grants (`cv_app`: SELECT, INSERT, UPDATE, DELETE) and the privilege manifest are updated. Rollback: `db/rollback/104_candidate_matrix.down.sql` (not a migration; run on a restored copy first) removes the layouts and `review_reason`; applications, scores, runs, item evaluations and gate results are untouched. Tested for upgrade, rollback and re-application.

## Known limitations

* Candidate position and location are not stored, so those columns are unavailable; age is not offered.
* Conflicting-evidence flags depend on the evaluator marking a conflict; unmarked conflicts appear as *Unclear*.
* The tenant default cannot name criteria columns. Personal views are per job (not global across jobs).
* The export is CSV only and capped at 5,000 rows; there is no tenant logo in it.
* Column reordering supports buttons and pointer drag-and-drop; there is no keyboard drag.
