# Bounded evaluation of the staged approach and of the validation layer: PROPOSED protocol

Status: **proposal, not executed.** Recorded before any call so that its gates and stopping rule are fixed in advance. It is not active,
authorised or budgeted. Running any model call under it needs explicit approval, a budget ceiling set by the owner, and a re-pin of this file.
Nothing here changes the active prompt, the frozen labels, the scorer or any stored score.

## 1. Questions

- **Q1 (no model call):** does the validation layer detect seeded defects (the six mutation types) and surface real defects in
  independently reviewed answers, and how often are its review flags confirmed by reviewers?
- **Q2 (model calls):** does a staged pipeline recover more requirement statements than the current single call, without more wrong
  routing, OR/AND defects or unacceptable cost and latency?

## 2. Cases

- **Set A (independent gold, required):** 24 job descriptions, 12 English and 12 Arabic, drawn from material not used to write any prompt.
  Each has a statement-level gold set made by two reviewers working independently and blind to arm: for each source statement, whether it is a
  requirement, duty, non-scoreable requirement, post-hiring condition, information or context; its category; Required or Preferred; OR
  alternatives; and AND separation. Disagreements are adjudicated by a third reviewer. Inter-rater agreement (Cohen's kappa) on the
  requirement-versus-context label is reported. **If kappa is below 0.6, the reviewer procedure is revised before execution.**
- **Set B (calibration, not scored):** the two existing EN and AR references and TC20. They tune nothing and report nothing as test results.

## 3. Arms

- **Single call (SC):** the current production-shaped extraction, one call per JD, with the prompt in force at the time of the evaluation.
- **Staged (ST):** stage 1 inventory, stage 2 classification, stage 3 structuring in one or more calls, stage 4 validation with the
  prototype. The exact prompts are pinned before execution.
- **Validation layer alone (VL):** applied to the SC answers, with no extra call. Used for Q1.

## 4. Measures

- Requirement-statement recall: the share of gold requirement, duty and non-scoreable statements covered by a verbatim quotation in the answer.
- Routing error rate: the share of gold conditions in the wrong list.
- OR and AND defect rate: the share of gold OR or AND groups that are not kept or split as gold says.
- Flag precision: the share of VL review flags that reviewers confirm as real defects; flags are reported per type.
- Seeded-defect recall: per mutation type (omitted section, wrong list, lost OR, bundled AND, shorter quotation, ambiguous category).
- Model calls, tokens, cost and wall-clock latency, measured, per JD and per arm.

## 5. Acceptance criteria (fixed here)

For Q1 (validation layer, no model call):
- Seeded-defect recall at least 0.95 for each of the six mutation types on Set A.
- A flag type is recruiter-facing only if its precision is at least 0.60 on Set A. A flag type below 0.60 stays a diagnostic.

For Q2 (staged versus single call), measured separately for English and Arabic:
- **Superiority on requirement recall:** ST minus SC is at least +0.05, and the 95 % paired-bootstrap interval for the difference has a lower bound above 0.
  If the interval includes 0, the result is "no demonstrated improvement", and the staged approach is not adopted.
- **Non-inferiority on routing and OR/AND:** ST minus SC is no greater than +0.02 absolute on routing error and on OR/AND defect rate. The margin is
  stated here and is not chosen after the data are seen.
- **Cost and latency:** ST cost per JD at most 2.0 times the measured SC cost, and ST wall-clock latency at most 2.5 times SC. Exceeding either
  rejects the staged approach, whatever the recall result.
- **Failure:** a stage that fails stops the JD's pipeline and is recorded. There are no retries, following the earlier experiments.

Failing a gate is a result, not an error to repair. Passing every Q2 gate on both languages would justify a follow-up protocol. It would not
justify activation.

## 6. Stopping rule (fixed before execution)

1. Run the first 12 JDs (six English and six Arabic) with both arms.
2. If ST recall is lower than SC recall in **both** languages, stop the staged work and report the result.
3. Otherwise run the remaining 12 JDs. Decide every gate in section 5 on all 24 JDs. Do not add JDs, arms or calls after this point.
4. An inconclusive gate (its interval includes the margin) is reported as inconclusive. It is not resolved by more calls under this protocol.

## 7. Budget and reporting

- Expected cost before execution: single call about 0.0355 USD per JD (measured), so 24 JDs about 0.85 USD for SC; staged estimated at 1 to 2 times
  that per JD. **The owner sets a ceiling before execution.** The ceiling must cover the worst case of the planned calls, computed as in the
  earlier experiments.
- Outputs go to a new or empty directory with a manifest that pins every prompt, JD, reviewer gold file, protocol hash and script hash.
- Original scores, labels and scorers are not changed. Supplementary diagnostics are reported separately from the gates.

## 8. Out of scope

- Activation, migration, deployment, production integration and any change to the frozen `requirements_v2` package.
- Recruiter-facing display of the review queue.
- Generalisation beyond English and Arabic job descriptions of the kind in Set A.
