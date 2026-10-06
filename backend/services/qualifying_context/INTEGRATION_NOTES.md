# Qualifying context — integration notes

## Status

| Phase | State |
|---|---|
| P1 service (schema, strict parse, grounding, pinned runner, prompt copy) | done |
| P2 criteria-worker integration + persistence | done; behind `system_config` key `scoring_v2.qualifying_context_analysis_enabled` (only exact `"true"` enables; missing row / error / other value = OFF) |
| P3 recruiter review / confirm / edit (API, details review status, UI) | done; works whether or not the flag is on, UI hidden when `not_assessed` |
| P4+ S1 agreement, S2 contexts, orchestration | not started |

Production scoring (D-01 / `criteria_matcher` / F-01) does not read qualifying context.

## P2 persistence contract

- `analysis_json.experience.qualifying_context`: the frozen `{state, contexts, source}` object; absent = not assessed.
- `analysis_json.qualifying_context_audit`: `{"schema": "qc_audit_v1", "current": ..., "latest_run": ...}`;
  `current` describes the stored object, `latest_run` the last attempted run (with the AI reading in `result`).
- Merge rules: `services/qualifying_context/persistence.py` (module docstring).
- `original_analysis_json` gets an AI-only candidate (`:orig`) through the existing COALESCE; it never
  captures a recruiter-owned object.

## P3 recruiter contract

- `POST /jobs/{id}/criteria/qualifying-context/confirm` `{expected_qualifying_context}` — confirm an automatic
  identified/none suggestion unchanged (`recruiter_confirmed`).
- `PUT /jobs/{id}/criteria/qualifying-context` `{state, contexts, expected_qualifying_context}` — set identified
  (1–10 contexts, ≤200 chars, trimmed only, no grounding) or none (`recruiter_edited`). Never `uncertain`.
- admin / hr_manager only (super_admin 403, as `criteria/content`); tenant-scoped (other tenant → 404).
- One transaction: `SELECT … FOR UPDATE OF jc` → compare stored object with `expected_qualifying_context`
  (409 `qualifying_context_changed` / `nothing_to_confirm`) → UPDATE `analysis_json` only → strict
  `audit_logs` row (`qualifying_context_confirmed|edited`) → commit. An audit failure rolls the change back.
- `qualifying_context_audit.current` carries recruiter provenance (one-level `previous`); `latest_run` is never
  touched; `original_analysis_json` is never touched.
- `PUT /criteria/content` now reads `analysis_json` with `FOR UPDATE` (only change to that endpoint).
- `GET /jobs/{id}/details` adds `qualifying_context_review` (`recruiter.review_status`).

### Manual QA checklist (no frontend test runner exists; `tsc --noEmit` error set unchanged)

1. Job with no context and no audit → section absent (never "No additional … restriction").
2. Failed automatic assessment → amber warning; Edit only; no Confirm.
3. AI uncertain → "Needs confirmation" text + possible restriction chips; Edit only; no Confirm.
4. AI identified → contexts as chips + "Suggested automatically — not yet confirmed"; Confirm + Edit.
5. AI none → "No additional qualifying context restriction." + suggestion note; Confirm + Edit.
6. Confirm → "Confirmed by {name} on {date}"; Confirm button gone; Edit remains.
7. Edit → Yes with lines / No; Yes with no lines shows the validation message; save → "Set by {name} on {date}".
8. Viewer / recruiter roles → value visible, no buttons.
9. Two tabs: confirm in one, edit in the other → conflict message shown and the latest value reloaded.
10. Arabic UI → all strings Arabic, layout RTL, chips and radio buttons aligned right.
11. No internal terms, prompt/model names or raw model output anywhere in the section.

## Existing issues recorded, NOT addressed by P2

1. Re-extraction (`POST /criteria/retry`, Celery retries) fully replaces `analysis_json`, the flat criteria
   columns and the `weight_*` columns: recruiter edits to skills, soft skills, `minimum_years`,
   `relevant_roles`, education, certifications, domain knowledge, other requirements and weights are lost.
   `last_edited_by/at` survive, so the job still looks recruiter-edited. (Only qualifying context is protected.)
2. (P3: its `analysis_json` read now takes `FOR UPDATE`, which closes the qualifying-context race; no other
   change.) Before P3, `PUT /jobs/{id}/criteria/content` did an unlocked read-modify-write; it could race with
   the worker and write stale content (including a stale qualifying-context object/audit) back over a fresh
   analysis. P3 recruiter QC endpoints must lock the row (`SELECT ... FOR UPDATE`).
3. Editing the job description (`PUT /jobs/{id}`) does not re-run extraction, and completed jobs cannot be
   retried; the analysis can silently go stale. Qualifying-context staleness is detectable by comparing
   `sha256(jobs.description)` with `qualifying_context_audit.current.jd_sha256`.
4. `original_analysis_json` is set by the first write ever, which can be an `insufficient` analysis.
5. `job_criteria.ai_model` records `settings.openai_model`, not the registry model actually used.
6. The retry endpoint checks status then updates without a lock, so a double click can enqueue twice.
7. A failed DB write makes Celery repeat the main criteria-extraction LLM call (and the QC call).
8. `CriteriaMatchEngine.criteria_version` hashes the whole `analysis_json`, so it changes whenever any
   key changes (trace value only; no decision depends on it).
9. There are no endpoint tests for `/criteria/retry` or `/criteria/content`.
10. SECURITY: `PUT /jobs/{id}/criteria` (scoring weights) has no server-side role check — any authenticated
    user of the tenant (including recruiter / viewer roles) can change weights; only the UI hides the control.
    Not fixed in P3.
11. Routers cannot be imported in this test environment (a `cryptography` Rust-binding panic, present at the
    P2 baseline too), so router wiring is covered by static source checks, not endpoint tests.
