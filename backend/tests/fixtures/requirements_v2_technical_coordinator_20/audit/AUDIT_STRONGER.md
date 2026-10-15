# Audit: the stronger-model run (v2-3 full prompt, gpt-4.1-2025-04-14)

Status: **provenance verified; scores reproduced with the frozen scorer; raw answers audited.** This audit made no paid call.
Evidence: `audit/run_tc20_v2-3-full-stronger/` (copies of the uploads, byte for byte, plus `analysis.json`).

## 0. Provenance

| File | sha256 | Upload |
|---|---|---|
| `calls.jsonl` (5 rows) | `65293984cf5d911c59a54678eb09851a6f6e6f8a362ee35633fcb676be9f255d` | `4ef01968-calls.jsonl` |
| `manifest.json` | `bad16da9293389b8c1e6281ea3f7ec86b599b63bde8684775260ec831451f92c` | `5ba8ec65-manifest.json` |
| `scored.json` | `234a9ff4ec7fefb511a1fa68b3926aa47f08a8cd0251cd7bed190c62c0b7065e` | `6eed1fd1-scored.json` |

- Prompt: manifest prompt sha256 `21a2f942…b784b43b` = the unchanged full v2-3 file. Labels `6a87da80…`, JD `c0c7132c…`, user message `ff6b334a…` all match.
- All five rows carry the manifest's input hashes; all five returned `gpt-4.1-2025-04-14`, no errors.
- Spend: 0.14685 USD (sum of the five per-call costs, each recomputed from the usage at $2/M input and $8/M output), against the $0.40 cap and a worst case of 0.38734.
- The manifest does not record the git commit of the VPS checkout. The run's commit is therefore not proven by the files; the preflight in `EVALUATION.md` must be filled in by the owner.
- Reproduction: `requirements_v2_tc20_compare.report(calls, jd, labels)` with the frozen scorer returns exactly the report in `scored.json`.

## 1. Scores (reproduced)

| Check | Result | Calls passing |
|---|---|---|
| C_RESP (16 duty lines) | 2/5 | calls 1, 2 |
| C_COMP (three competency lines) | 5/5 | all |
| C_EXP_OR (experience alternatives) | 2/5 | calls 1, 5 |
| C_FAM_OR (familiarity "or" list) | 0/5 | none |
| C_LOCAL (domain knowledge) | 5/5 | all |
| C_LOCATION (work location condition) | 5/5 | all |
| C_REPORTING (reporting line) | 5/5 | all |
| C_AND (soft skills split) | 0/5 | none |

Reported separately (not official): education-alternative retention 5/5; schema compliant 5/5; category weights usable 5/5 and totalling 100 in 5/5; invented source text 0; invented duties 0.

Comparison with the stored gpt-4o-mini v2-3 baseline (same prompt, same JD): C_COMP 0→5, C_LOCAL 0→5, C_LOCATION 3→5, C_REPORTING 0→5, C_RESP 0→2, C_EXP_OR 0→2; C_FAM_OR and C_AND unchanged at 0.

## 2. Findings in the raw answers

**Duties are unstable across calls (the largest defect).** Calls 1 and 2 extract all 16 duty lines as `from_responsibilities` experience items. Calls 3, 4 and 5 extract none (`origin: from_responsibilities` count 0), so the experience list shrinks from 18–19 items to 3. The same JD and prompt gave a complete and an empty duty section, so this is run-to-run variance at temperature 0.1, not a parser effect. Two of five calls is not a reliable result.

**Experience alternatives (C_EXP_OR) fail in three of five calls.** Calls 2, 3 and 4 keep the "1–3 years … ICT systems support, business applications, digital platforms, or software implementation projects" item without its alternatives list (`alternatives: null`). Calls 1 and 5 keep the four options as alternatives. The `experience.subject` still carries the full phrase in all five calls.

**Familiarity alternatives (C_FAM_OR) fail in all five.** The "Familiarity … or enterprise applications" line is kept as one item with no alternatives. In call 2 it is also moved to `skills`.

**Soft skills stay in `skills` in all five calls (C_AND).** `soft_skills` is empty in every call; the three communication, coordination and teamwork lines sit in `skills`, next to the technical ones. The baseline also left this check unmet.

**Arabic and English are merged in all five calls.** One item "Good command of Arabic and English, including …". The stored gpt-4o-mini baseline split them into two items in all five calls, so this is a change from the baseline, not a regression in content.

**JavaScript stays in `skills`** (preferred, "is considered an advantage"), not in `experience`. It is the baseline's category, so the category defect of the no-examples run does not recur.

**Weights are whole numbers totalling 100 in all five** (for example 30/40/20/0/0/10/0 in call 1; 40/30/30 in calls 3–5). They are usable under the parser's normalization without any correction.

**Education alternatives are kept in all five** (Computer Science, Information Technology, Software Engineering, Information Systems, a related field).

**Source text is verbatim** (no invented source, no invented duty in any call). Items sometimes drop the final full stop in `text`; that is cosmetic.

**Schema is complete in all five** (no missing keys, no invalid enums). Unlike the no-examples run, no `alternatives` or `experience` key is absent.

## 3. What this run shows, and does not show

- A stronger model fixes most of the failures measured in the stored gpt-4o-mini run and in the no-examples run: competency lines, local-knowledge routing, location and reporting routing, and schema compliance.
- It does not yet reliably extract duties (2 of 5) or keep the alternatives (C_EXP_OR 2 of 5, C_FAM_OR 0 of 5). These are the failures that matter most for scoring, and they vary between calls of the same prompt.
- One JD and five calls per arm: the variance seen here (duties 16 versus 0 with the same input) means no single run, of either model, is a stable measurement. A conclusion about the model needs more calls or more JDs; this run alone does not establish that gpt-4.1 is better or safe to use.
- Model and prompt cannot be separated by this run; the comparison was designed so that only the model changed, but the pricing and the snapshot were recorded from the owner's reading of the official page and were not re-read here.

## 4. Next step (not run)

Repeat the same arm to measure variance (the duty extraction in particular) before deciding anything; the prompt and labels stay unchanged.
