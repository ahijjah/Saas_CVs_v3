# Audit of the Example 5 removal experiment (commit 4ea4323): status

## Status: the experiment's four files were NOT attached, so the run audit is not done

The four files attached to this request are **not** the experiment's files. They are the TC20 repeat's files (commit `23cce8e`, protocol
`860eed9e…`, spend 0.391792), and their hashes equal the repeat files already stored in `tests/fixtures/requirements_v2_tc20_repeat/run/`:

| Attached file | sha256 | Equals stored repeat file |
|---|---|---|
| `calls.jsonl` | `1c3c9bb12c0c47480dbd015694b9766e11ab0287fd449d605d52c1b19265d9ed` | yes |
| `manifest.json` | `512536540b4b5c4e20f9e425d80b39ea00ff9319df4585512be941625094e3ba` | yes |
| `run.json` | `3177650783e65ffbedb8868db1054d2c97311167b179fc2731a0d74bb22a3527` | yes |
| `scored.json` | `2a3a240885d1d1f477ca8a053c9e0b4047186a019d449497f6903ce64fd50edb` | yes |

The experiment's manifest must name protocol `5a7412438a42cdc50f4e41b847031584a1c1a0de34438eeea274650abad853be` and commit
`4ea4323b206f6e32307c902d72d65e5c2a1bbff9`, and its variant pin is `7cf171c8…66e62`. None of that is present. Nothing in this
document reports a result from the experiment's calls. The numbers below for FULL and VARIANT come from your message and are not verified.

To complete the audit, re-attach the experiment's four files byte for byte (`manifest.json`, `calls.jsonl`, `run.json`, `scored.json`).

## Static analysis (no run files needed): the coverage instruction and rule 9

Exact text of v2-5 (quoted from the prompt, not paraphrased):

**COVERAGE:** "Before you write the answer, go through the job description section by section, in the order the sections appear: each
heading, each list and each sentence, in both languages. Every requirement, duty and competency line found in a section becomes an item
(rules 4 to 8) or a condition (rule 9)."

**Rule 9:** "Each statement goes in exactly ONE of three lists. Choose the list by what the statement is, with this table …
informational_items: company_description (…), benefits (…), reporting_line, hr_statement (equal-opportunity and similar statements)."
and "post_hiring_conditions: … document_submission."

### Scope gap between the two instructions

1. **The walk and the obligation cover different things.** COVERAGE walks "each sentence", but its obligation names only "requirement,
   duty and competency line". A reporting sentence is walked and is not named in the obligation, so the instruction does not say where it
   goes. Rule 9 says "Each statement goes in exactly ONE of three lists", which is broader. The two instructions do not agree on whether
   a statement that is none of requirement, duty or competency must be output.
2. **COVERAGE never names reporting.** The word "reporting" does not occur in the coverage paragraph (checked).
3. **The reporting label is undefined.** `reporting_line` is listed in rule 9 without a description. In v2-3 and v2-5 it occurs only in the
   rule 9 table and the JSON enum. The only worked answer that uses it is Example 5, which is the variant's removed block.
4. **A lexical overlap with a post-hiring label.** The TC20 sentence "The Coordinator shall submit deliverables to the IPSD II Technical
   Resident Advisor and the IPSD II Project Director for review and approval." contains "submit deliverables", and rule 9 lists
   `document_submission` under `post_hiring_conditions`. The rule gives no test to separate a submission that happens after hiring from a
   statement of who receives deliverables during the work. The labelled check looks only at the informational list, so this ambiguity is not
   scored.

The coverage paragraph and rule 9 are identical in v2-3 except that v2-3 has no COVERAGE paragraph. Rule 9 is byte-identical between the arms.

## What the stored evidence shows, by run (not pooled)

Reporting statement ("submit deliverables") present in the whole raw answer, per 5 calls:

| Run (stored, verified) | v2-3 | v2-5 |
|---|---|---|
| Paired run, 22-call audit | 4 of 5 | 1 of 5 |
| Repeat, 10-call (`run/`) | 5 of 5 | 0 of 5 |
| Example 5 experiment, FULL (as reported in your message, unverified) | not run | 4 of 5 |
| Example 5 experiment, VARIANT (as reported in your message, unverified) | not run | 0 of 5 |

The FULL arm (the same prompt as v2-5) therefore ranges from 0 to 4 of 5 across three runs. Any 5-against-5 comparison of one change is
sensitive to that variance. The reported VARIANT result (0 of 5) does not improve on FULL's result in the same run. This weakens the
hypothesis that Example 5 causes the omission, and it does not establish causation or rule out interactions with other instructions.

## Recommendation (one minimal controlled experiment; not run; no budget committed)

**The single change:** in the COVERAGE paragraph, replace the obligation sentence

> "Every requirement, duty and competency line found in a section becomes an item (rules 4 to 8) or a condition (rule 9)."

with

> "Every requirement, duty, competency line and statement found in a section becomes an item (rules 4 to 8) or a condition (rule 9)."

This makes the obligation match rule 9's "Each statement goes in exactly ONE of three lists", and names the case the paragraph leaves out.
Nothing else changes; Example 5 stays in place, so the change is independent of the removal experiment.

**Arms and calls:** FULL (`criteria_extraction_v2-5` as it is) against FULL-with-the-one-sentence-change, five alternating calls each, same
TC20 JD, job context, user message, model and settings as before.

**Budget:** ten calls, worst case about USD 0.81 (the same estimator as the removal experiment), with a hard cap of USD 1.00.

**Decision rule, fixed before the run:** the scope correction is supported only if the changed arm keeps the reporting statement in the whole
answer in four or more of five calls **and** FULL, run in the same batch, keeps it in two or fewer. Because FULL has varied from 0 to 4 of 5,
a result that falls between those values is inconclusive. Duty, OR and AND results are reported separately and do not decide the
reporting question. v2-5 stays inactive whatever the result.

Not recommended: another removal experiment. The removal test has been run once, its VARIANT did not improve reporting, and the coverage
scope gap above is a concrete textual reason to test the next change instead.
