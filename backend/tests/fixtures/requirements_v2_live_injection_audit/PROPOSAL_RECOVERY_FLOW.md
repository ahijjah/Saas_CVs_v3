# Proposal (not implemented): versioned recovery for a blocked analysis

Status: proposal only. Nothing here is built. It answers the open decision from the implementation note: how a job whose analysis is stale (its
description or context changed, or its context cannot be verified, or it carries an AI-directed context field) can be unblocked without losing the
original evidence or the recruiter's edits.

## Why it is needed

Today a job with a draft cannot be re-analysed: the retry endpoint works only for jobs with no document, and the claim requires no original
snapshot. So the only way out of an inputs block is to restore the old inputs, which is not a recovery for a real change.

## Principles

1. The original extraction is immutable evidence. Every analysis keeps its own original snapshot and record; none is overwritten.
2. A stored record is never rebound to new inputs. A new analysis is a new version, bound to the inputs it was made from.
3. The recruiter's draft is never silently replaced. Applying a new analysis is an explicit, audited action.
4. The existing gates are unchanged: a candidate version with its own blockers stays blocked.

## Model

- New append-only table `requirements_analysis_versions` (one row per version: `job_id`, `version`, `original` JSON, `record` JSON, `created_by`,
  `created_at`, `attempt_token`). An update or delete trigger refuses changes. Version 1 is the existing `original_analysis_json` and record; it is
  not copied or rewritten.
- The draft in `analysis_json.requirements` stays where it is, with a pointer `based_on_version` to the version it was last applied from or edited
  against. Editing never changes that pointer by itself.

## Flow

1. **Request a new analysis** (admin or HR manager, feature switch on, role and job access checked, explicit button). Creates a new attempt
   under the criteria lock. Allowed only when the current analysis is stale or blocked, so routine re-analysis of a clean job is refused.
2. **Worker** runs the existing extraction and writes a candidate version (never the draft). Its binding, context review and blockers are computed as
   today. The claim changes from "no snapshot exists" to "this attempt is the active candidate for this job". The approved pipeline is unchanged.
3. **Review the candidate.** The editor shows the candidate beside the draft, with the existing comparison view, and the changed inputs.
4. **Apply** (explicit). Three options, all audited:
   - *Keep the draft*: the draft stays, still bound to its old inputs, still blocked. The candidate remains available.
   - *Take the candidate*: the draft is preserved as a superseded version, and the candidate becomes the draft and `based_on_version`.
   - *Merge*: a three-way merge with the existing `utils/requirementsMerge` helper: base = the original of the draft's version, ours = the draft, theirs =
     the candidate's original. Conflicts are shown and resolved by the recruiter.
   Apply runs under the criteria lock with the expected revision, so a concurrent edit gets the existing conflict resolver. A candidate whose own inputs
   changed again is stale and cannot be applied.
5. **Progression** is recomputed by the existing gates after apply. Nothing is marked passed by the apply itself.

## What does not change

- `original_analysis_json` and the stored records of earlier versions are never rewritten.
- The binding rule: a difference between an analysis's bound inputs and the job's current inputs is always a blocker.
- Context findings and injection blockers are recomputed on the candidate, never copied from the draft.

## Audit

`requirements_analysis_requested`, `requirements_analysis_candidate_stored`, `requirements_analysis_candidate_applied` (with the choice and the
version numbers), `requirements_analysis_candidate_discarded`. Each carries the inputs hash of the version it concerns.

## Test plan (when built)

Real PostgreSQL, as for the existing suites: a version row cannot be updated or deleted; applying a candidate keeps the superseded draft and the
original byte-for-byte; a concurrent edit during apply is rejected; a candidate whose inputs changed again cannot be applied; a candidate with its own
injection blocker stays blocked; the merge conflicts are shown; the audit rows are committed; the browser flow for each option.

## Decisions needed before building

- D-R1: allow re-analysis of jobs that have a draft (this proposal) or only failed attempts (today).
- D-R2: who may apply a candidate: the same roles as editing, or admin only.
- D-R3: retention of superseded drafts and candidates (kept indefinitely is the simplest and matches the audit requirement).
- D-R4: whether the "keep the draft" option may be used to publish a draft that is still bound to old inputs (proposed: yes, it stays blocked).
- D-R5: a version table (proposed) versus versions inside `analysis_json` (smaller change, larger row, weaker append-only guarantee).
