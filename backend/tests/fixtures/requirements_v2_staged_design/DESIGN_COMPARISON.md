# Staged extraction versus the current single call: design comparison and recommendation

Status: offline design work. No model call, no new active prompt, no migration, deployment, activation or production integration. The
prototype is `scripts/requirements_v2_staged_check.py` (Stage 1 inventory and Stage 4 validation, deterministic, no model).

## 1. What is compared

- **Current:** one call returns the whole contract (categories, conditions, weights, warnings). The active prompt is v2-3 (control);
  v2-5 and its variants are offline candidates.
- **Staged (proposed):** four stages. (1) identify source statements across the whole JD with verbatim spans and stable references;
  (2) classify each statement as requirement, duty, non-scoreable requirement, post-hiring condition or information; (3) structure requirement
  items (category, Required or Preferred, OR alternatives, AND separation, experience); (4) validate coverage and routing, and surface the
  unresolved cases for recruiter review.

## 2. Measured baseline (stored evidence only)

| Quantity | Value | Source |
|---|---|---|
| Single-call cost, TC20, per call | mean 0.0355 USD (range 0.0342 to 0.0434) | routing run, 20 calls |
| Prompt tokens per call | mean 7,642 (range 7,625 to 7,660) | same |
| Completion tokens per call | mean 2,526 (range 2,363 to 3,513) | same |
| Prompt size | 29,372 characters (v2-5 family) | prompt file |
| TC20 JD size | 5,546 characters, 40 statements, 10 headings | prototype |
| Latency | **not recorded** in any stored evidence | none |

## 3. Tradeoffs

| Criterion | Current single call | Staged | Evidence status |
|---|---|---|---|
| **Completeness** | Omissions are invisible to the call. A zero-duty answer (v2-3, paired call 1) returned no error. | The inventory (stage 1) and the coverage check (stage 4) make an omission visible, and a stage-3 group can be re-asked. Stage 1 misses remain invisible unless the inventory is independently checked. | Visibility: shown by the prototype on stored answers. Recall gain: **not measured**. |
| **Grouping accuracy** (OR, AND) | OR and AND rules are in the prompt. In the stored answers, the frozen C_EXP_OR check passes in 3 of 50 v2-5 family calls, C_FAM_OR in 2 of 50, and the communication line is split in 0 of 60 calls. | Per-group rules may help, but the grouping decision is still made by the model. | Staged benefit: **not measured**. Current failure: measured. The prototype flags the candidates but does not decide them. |
| **Routing** (conditions in the right list) | Misroutes occur (2 of 5 CLARIFIED answers). Rule 9 already has the table. | A stage-2 classifier with the table can route, and stage 4 checks it deterministically against the same table. | Deterministic check: **works offline**. Classification accuracy: **needs model evaluation**. |
| **Model calls per JD** | 1 | 3 to 5 (stage 1 may be deterministic; stages 2 and 3 are calls) | Design fact. |
| **Latency** | Unknown (one call) | Sequential stages add latency; stage-3 groups can run in parallel | **Not measured**. |
| **Cost** | 0.0355 USD per call (measured) | Estimated at roughly 1 to 2 times the single call: each stage repeats the JD, and the rules are split across stages, so input is repeated while output per call shrinks. | **Estimate only**. Must be measured in the bounded evaluation. |
| **Complexity** | One prompt, one parser | Four stages, three prompts, state passed between stages, more failure modes, a validator to maintain | Design fact. |
| **Editor compatibility** | The output is the contract the editor already consumes. | The final assembly can produce the same contract. The trace (spans, statement IDs, flags) has no field in the contract and must be stored beside it. The review queue needs a display path that does not exist yet. | Design fact. The contract is unchanged in this prototype. |
| **Auditability** | The raw response is the only record. | The raw response is kept, and every derived item can be traced to a verbatim span of the JD. | Works in the prototype (tested). |

## 4. What works with stored evidence, and what needs model evaluation

**Works offline with stored evidence (tested in `tests/test_requirements_v2_staged_check.py`):**
- A deterministic statement inventory with verbatim offsets and stable IDs, headings surfaced separately, lower-case continuations kept in one sentence.
- Verbatim location of every quoted source. Any quote that is not in the JD is refused, and whitespace-only differences are reported.
- Coverage: each statement is covered, covered only by a shorter quotation, or uncovered. Uncovered statements are surfaced, never filled in.
- Routing: each condition is checked against the rule 9 table, read from the prompt. Mismatches, `other` and unknown labels are surfaced.
- Possible lost OR (text has an OR and the item has no alternatives) and possible bundled AND (text joins items and one item carries them).
- Real evidence: the real omission in the zero-duty answer, the real wrong list in CLARIFIED calls 2 and 5, the real reporting merge, and the OR flags that agree with the frozen OR checks in the 30 stored answers that were checked.
- Mutations: omitted section, condition in the wrong list, lost OR alternatives, bundled AND, shorter verbatim quotation, non-verbatim quotation, ambiguous category.

**Still requires model evaluation (not decided by the prototype):**
- Whether a statement is a requirement, a duty, a condition or context. An uncovered statement may be context (for example the objective paragraph), a duty not extracted, or an omission. The prototype cannot tell which.
- The category of an item (for example experience or skills). A wrong item category is not detected by text matching (pinned by a test).
- Required or Preferred. Not checked.
- Whether an OR or AND flag is correct. The flags are candidates; rule 5's "independent requirement" is a judgement.
- Attached Arabic و (AND) is not detected (pinned by a test).
- Semantic completeness of any kind.

## 5. Recommendation

1. **Adopt the validation layer first, as a review aid, without changing the prompt or the call count.** It addresses the omission and routing
   failures the single call cannot report about itself, costs no model calls, works on stored answers, and can be checked offline. Its flags are
   candidates for recruiter review, not decisions.
2. **Do not adopt the staged model calls yet.** Their benefit is unmeasured and their cost and latency are unknown. Decide on them only through
   the bounded evaluation in `EVALUATION_PROTOCOL.md`, which is written before any call and has pre-set gates.
3. **Keep the current single-call approach** as the production path. Nothing in this design changes it.
4. Before any recruiter-facing use, measure the precision of each flag type on independently reviewed cases. The bundled-AND candidates
   are numerous (12 per answer on TC20), so an unmeasured flag would add review burden without evidence that it helps.

## 6. Remaining limitations

- Text matching is not semantic completeness. The coverage check shows whether a statement is quoted, not whether it was understood or classified correctly.
- The statement inventory is heuristic: sentences split at terminal punctuation followed by a capital, digit, quote, bracket or Arabic letter; headings are lines
  that are not bullets, end without terminal punctuation, and have at most eight words. A requirement written as such a line would be surfaced as a heading,
  not classified. Headings are listed in the report for this reason, but no check reviews them.
- The OR and AND checks use markers only (English "or", "either", "and", comma-plus-and; Arabic standalone أو and و). They do not parse grammar, so
  lists of verbs and lists of objects are flagged as candidates, and attached Arabic و is missed.
- The validator is evaluated on two reference JDs and the stored TC20 answers. Those are the only cases available, and the references are the
  authors' own expected outputs, not independently reviewed. No detection rate or precision is established.
- The label-to-list mapping comes from rule 9 and inherits its gaps: `other` is never mapped, and a label the prompt does not name is surfaced as unknown.
- The contract has no trace field. Storing traces beside the contract and showing the review queue are integration work, not done here.
- Latency and cost of a staged pipeline are unmeasured.
