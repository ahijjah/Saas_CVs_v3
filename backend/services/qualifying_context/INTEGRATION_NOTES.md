# Qualifying context — integration notes

## Status

| Phase | State |
|---|---|
| P1 service (schema, strict parse, grounding, pinned runner, prompt copy) | done |
| P2 criteria-worker integration + persistence | done; behind `system_config` key `scoring_v2.qualifying_context_analysis_enabled` (only exact `"true"` enables; missing row / error / other value = OFF) |
| P3 recruiter edit / confirm API | not started |
| P4+ S1 agreement, S2 contexts, orchestration | not started |

Production scoring (D-01 / `criteria_matcher` / F-01) does not read qualifying context.

## P2 persistence contract

- `analysis_json.experience.qualifying_context`: the frozen `{state, contexts, source}` object; absent = not assessed.
- `analysis_json.qualifying_context_audit`: `{"schema": "qc_audit_v1", "current": ..., "latest_run": ...}`;
  `current` describes the stored object, `latest_run` the last attempted run (with the AI reading in `result`).
- Merge rules: `services/qualifying_context/persistence.py` (module docstring).
- `original_analysis_json` gets an AI-only candidate (`:orig`) through the existing COALESCE; it never
  captures a recruiter-owned object.

## Existing issues recorded, NOT addressed by P2

1. Re-extraction (`POST /criteria/retry`, Celery retries) fully replaces `analysis_json`, the flat criteria
   columns and the `weight_*` columns: recruiter edits to skills, soft skills, `minimum_years`,
   `relevant_roles`, education, certifications, domain knowledge, other requirements and weights are lost.
   `last_edited_by/at` survive, so the job still looks recruiter-edited. (Only qualifying context is protected.)
2. `PUT /jobs/{id}/criteria/content` does an unlocked read-modify-write of `analysis_json`; it can race with
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
