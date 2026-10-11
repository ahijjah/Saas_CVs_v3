# Requirements-v2 editor: real-page verification

Real FastAPI router + services + disposable PostgreSQL (migrations 106/107) + Chromium driving the real editor component. Synthetic jobs only. No AI call, no network, no VPS.

44 of 44 checks passed.

- PASS S1 injection and split-OR blockers are shown prominently (red alert section above readiness)
- PASS S1 injected requirement shows the AI-directed text from the job description
- PASS S1 weights contamination shows applied 50% and proposed 100%
- PASS S1 split-OR shows the options and the shared sentence
- PASS S1 no acknowledgment control exists for injection or split-OR
- PASS S1 classification and conflict issues are listed by gate
- PASS S1 conflict shows both job-description statements as evidence
- PASS S1 provenance lists prompt version and model
- PASS S1 raw AI output is collapsed for an editor
- PASS S1 raw AI output expands to the stored response
- PASS S1 readiness reflects the server state and is not green
- PASS S2 removing the injected item changes only the draft (unsaved badge, no write request, revision unchanged)
- PASS S2 the issue is marked as corrected in the draft but still listed until saved
- PASS S2 stored-state actions are disabled while unsaved changes exist
- PASS S2 'edit weight' focuses the category weight input
- PASS S2 keep-one removes the redundant split item and keeps one
- PASS S2 weights were NOT redistributed (category weight unchanged; totals flagged)
- PASS S2 invalid weights are rejected by the server (422) and nothing is written
- PASS S2 after explicit Equalize and weight edits the valid blocked/partly corrected draft saves (revision +1)
- PASS S2 blockers are gone after the server re-checked
- PASS S2 the stored record resolved the injection and split-OR issues (audited)
- PASS S2 original_analysis_json untouched
- PASS S3 classification and conflict acknowledgment buttons are enabled on the saved version
- PASS S3 requests use the existing endpoint with gate=classification then gate=conflict
- PASS S3 the job is ready only after both acknowledgments
- PASS S3 acknowledgments are server-owned records (audited)
- PASS S4 (setup via API) the blockers are corrected and saved
- PASS S4 policy No: readiness is Ready while classification and conflict stay visible
- PASS S4 flipping the admin setting to Yes (no write) makes the same saved job need review
- PASS S5 'Additional checks unavailable' is shown
- PASS S5 readiness is labelled as basic checks only (not green, no claim of passing)
- PASS S5 no issue panels claim 'no issues'
- PASS S5 the original analysis stays available for comparison
- PASS S6 a clear blocking message is shown for the damaged record
- PASS S6 the original analysis and the requirements stay readable
- PASS S6 a write is refused (409) with a clear message and nothing is stored
- PASS S7 Arabic: the editor is right-to-left
- PASS S7 Arabic: blocker, status and gate strings are Arabic
- PASS S7 Arabic: English JD evidence keeps its own direction (dir=auto)
- PASS S8 Arabic job-description evidence is displayed with its own direction
- PASS S9 a non-editor sees the issues but no correction, save or acknowledgment control
- PASS S9 the raw AI output is not offered to a non-editor
- PASS S10 a valid document with unresolved blockers saves (revision +1) and stays blocked
- PASS S11 a concurrent change opens the existing conflict resolver, the draft is kept and nothing was overwritten
