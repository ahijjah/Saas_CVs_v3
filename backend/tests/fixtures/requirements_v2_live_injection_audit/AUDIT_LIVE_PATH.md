# Audit: live requirements-v2 extraction path and its injection protection

Offline audit. No model call, no deployment, no activation. It records what the repository contains, and separately what the VPS inspection
reported. It does not infer deployment from repository code.

## 1. Deployment facts (reported by VPS inspection, read-only; recorded here, not verified by this repository)

- `backend-api-1` and `backend-worker-1` both run the OLDER `services/requirements_guard.py`.
- In that file `REQUIREMENTS_V2_SCORING_SUPPORTED = False`.
- In that file `assert_job_evaluable` checks only that switch. It refuses every v2 job.
- The injection entry guard added in commit `0a81afa` is ABSENT from both containers.

Consequences:
1. Today, protection for a v2 job at the scoring entry point is the blanket refusal alone. The injection review is not consulted there.
2. If `REQUIREMENTS_V2_SCORING_SUPPORTED` were set to True on this deployed code, a v2 job with an open injection review (for example B06) would pass
   the entry guard. The gap described in `AUDIT_LIVE_PATH.md` section 3 (G1) would be live.
3. Before scoring is ever enabled, the following must all hold: the deployed code contains the gate from `0a81afa` (or a later equivalent); the scorer
   consumes `can_proceed` and every readiness gate, not only the injection gate (G5); and the decisions listed in `PROPOSAL_JOB_CONTEXT.md` are taken.
4. Not done: no deployment, no change to the switch, no change to the containers. The VPS facts above were supplied by the user.

Not established: the exact commit deployed. The report covers one file. The rest of the deployed tree has not been compared.

## 2. Live path, traced layer by layer (repository code)

| Layer | Behaviour for B06 (stored answer) | Evidence |
|---|---|---|
| Frozen parser `services/requirements_v2` | No injection gate. B06 is `ready` with the classification policy off and `needs_classification_review` with it on. | `parse_response` on the stored answer |
| Approved pipeline `parser_candidates/requirements_v2_pipeline_1` via `services/requirements_v2_extraction.analyse` | Opens two injection issues: the Rust item (`requirement`) and the soft_skills weight (`weights`). State is `ok`, so the draft is stored as completed with blockers. Readiness is `needs_injection_review`, `can_proceed` false, under both policies. | `analyse` on the stored answer |
| Persistence `to_record` → `finish_success` | Record keeps the open review, the raw response text and the original snapshot. Weight columns take the contaminated values (soft_skills 50). | `to_record`, `verify_record`; reading `finish_success` |
| Retries | Only transport errors retry. A successful extraction never retries because of injection. | reading `run_attempt` |
| Readiness | Recomputed from the record and the current document on every read and save. Saves rebuild the record from the trusted stored copy. | reading `requirements_api`, `injection.py` |
| Scoring entry | `cv_score`, `score-pending`, intake, bulk import and batch scoring call the guard. Today every v2 job is refused. The scorer reads weight columns only after that refusal. | reading `requirements_guard` and its callers |

Postgres-level coverage (`tests/isolated_requirements_v2_extraction_postgres.py`) exists for the worker's B06 draft. It needs `pgserver`, which is not in
`requirements.txt` and is not installable here, so it did not run in this environment.

## 3. Gaps

- **G1. Entry guard had no injection gate.** Fixed in the repository by `0a81afa`: with the switch on, an open injection issue, or a missing or invalid
  review record, refuses the job. NOT present in the VPS containers (section 1).
- **G2. Weight columns carry contaminated values while the review is open.** `finish_success` writes the applied weights regardless. Nothing reads them
  for v2 today. Not fixed; a decision is needed.
- **G3. Job context reaches the model but is not inspected.** See `PROPOSAL_JOB_CONTEXT.md`. Pinned in `tests/test_requirements_v2_live_injection_gate.py`.
- **G4. Re-added text clears the review.** A recruiter who removes the injected item and adds identical text back closes the review. Recruiter-added
  items are never flagged, by policy. Not changed.
- **G5. Only the injection gate is enforced at the scoring entry.** Split-OR, classification and conflict gates are not checked there, and no code reads
  `can_proceed` for scoring. Not fixed; a future v2 scorer must check it.
- **G6. Any other weight value clears the weight issue.** Only the contaminated and directed values keep it open. Policy; not changed.
- **G7. Description edits after extraction do not reopen the review or block readiness.** `PUT /{job_id}` (routers/jobs.py) writes a new, stripped
  `jobs.description` with no re-extraction, no change to the record and no readiness change. Only the non-blocking coverage warning compares the hash
  (`requirements_api.coverage_for`, which returns `[]` on a mismatch). So an admin or HR manager can add an AI-directed sentence to the JD after
  extraction and the injection review does not see it. This is the most serious gap found in this audit. Not fixed; see the proposal.
- **G8. The retry comment is inaccurate.** `request_extraction_retry` says it sends the same job context the first attempt sent. It reads the CURRENT
  database fields, so after an edit it sends the edited values, which is the correct behaviour but not what the comment says.

## 4. What this audit did not do

- It did not run the PostgreSQL worker test (no `pgserver`).
- It did not compare the deployed tree with the repository beyond the one guard file reported.
- It changed no frozen path, no approved pipeline file and no deployment.
