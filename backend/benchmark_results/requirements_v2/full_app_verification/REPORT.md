# Requirements-v2 editor: FULL-APPLICATION verification

Real FastAPI app (`main:app`, real JWT login), real SPA (JobDetails page) in Chromium, disposable PostgreSQL 16 with the project's schema and migrations. Synthetic v2 records inserted directly; no creation/extraction/evaluation, no AI call, no VPS.

45 of 45 checks passed.

Migrations that failed on top of schema.sql in the disposable database (pre-existing, not part of this scope): 010_platform_control.sql: trigger "trg_subscription_plans_updated_at" for relation "subscription_plans" already exists; 024_plan_features_seed.sql: Cannot seed plan_features: starter or professional plan not found. Ensure these plans exist in subscription_plans before running this migration.; 070_sla_thresholds_config.sql: syntax error at or near "->"; 087_autosave_deterministic_answers.sql: column "tenant_id" of relation "application_knockout_answers" does not exist

- PASS F1 real login form -> real /jobs/:id page (full JobDetails around the editor)
- PASS F1 the full JobDetails page still renders its other sections (CV ingestion / knockout area present)
- PASS F1 injection (requirement + weights) and split-OR blockers are shown
- PASS F1 the model conflict and classification warnings are listed
- PASS F1 no acknowledgment control for injection / split-OR
- PASS F1 readiness is the server's (blocked), provenance shown, raw output collapsed
- PASS F1 DEFECT FIX: /jobs/details no longer carries the pipeline record (raw output) to the page
- PASS F2 corrections change the draft only: nothing written, no auto-save
- PASS F3 invalid weights (required totals off after removals) are rejected: HTTP 422, no writes
- PASS F2 explicit save stores the corrected draft (revision +1, weight column synced, snapshot untouched)
- PASS F2 after a full page reload the blockers stay resolved (server re-checked)
- PASS F2 Edited badges appear on the corrected items/categories
- PASS F2 original comparison lists the removed items
- PASS F4 (setup via API) blockers corrected; only policy-governed reviews remain
- PASS F4 policy Yes: HR manager sees acknowledgment controls enabled on the saved version
- PASS F4 acknowledgments use the existing endpoint with gate=classification then gate=conflict, ready only after both
- PASS F4 policy No: Ready with classification and conflict visible but informational
- PASS F4 flipping the admin setting back to Yes (no write) re-blocks the same saved job
- PASS F5 viewer: issues visible, but no save / correction / acknowledgment / raw-output controls
- PASS F5 viewer: no API response carries the raw AI output or the stored record
- PASS F5 viewer: the API itself refuses writes (403)
- PASS F5 editors receive the raw output (collapsed) from the requirements API only
- PASS F5 another tenant's user cannot open the job's requirements
- PASS F6 no pipeline record: 'Additional checks unavailable', readiness labelled basic-only, original comparison available
- PASS F6 original comparison works without a record
- PASS F6 damaged record: clear blocking message, original and requirements readable
- PASS F6 damaged record: the write is refused (409) with a clear message and nothing stored
- PASS F7 stale save opens the conflict resolver; the admin's draft is kept, nothing overwritten
- PASS F7 after the merge both users' changes are in the saved version
- PASS F8 legacy job: the original criteria section renders and there is no requirements editor
- PASS F8 legacy job: the requirements API answers 409 and touches nothing
- PASS F9 ar_desktop: editor direction and language
- PASS F9 ar_desktop: blockers shown in the page language
- PASS F9 ar_desktop: no horizontal page overflow
- PASS F9 en_mobile: editor direction and language
- PASS F9 en_mobile: blockers shown in the page language
- PASS F9 en_mobile: no horizontal page overflow
- PASS F9 ar_mobile: editor direction and language
- PASS F9 ar_mobile: blockers shown in the page language
- PASS F9 ar_mobile: no horizontal page overflow
- PASS F10 keyboard: Enter on the raw-output summary expands it
- PASS F10 keyboard: the correction button is focusable and shows a focus ring
- PASS F10 keyboard: Enter performs the correction (draft only)
- PASS F10 keyboard: Space on Save sends the explicit save
- PASS F10 keyboard: Tab moves through interactive controls in order (no focus trap)
