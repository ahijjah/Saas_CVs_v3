# S1 two-pass — Pass A (target pass) real MAIN evaluation log

Audit record of every real Pass A evaluation run. The three `*.results.json` files next to this log are the
harness outputs (`scripts/s1_pass_a_eval.py`), copied byte for byte and SHA-256 pinned by
`tests/test_s1_pass_a_rollback.py`. They contain SYNTHETIC job descriptions and model outputs only: no candidate,
CV, application or personal data. Nothing in `services/` reads this directory.

Common setup of every run: fixture `main` (`s1_ctx_main_cases.json`, sha256 `cf5844a2…`, 44 cases / 45 criteria),
5 runs, model `gpt-4o-mini`, temperature 0.0, max_tokens 4000, client max_retries 0, no held-out fixture.
Hard gates: unsafe target/policy loss = 0, independence failures = 0. Acceptance gates: target, policy and
target_basis accuracy ≥ 0.95, stability ≥ 0.95, failure rate ≤ 0.05.

| File | sha256 | Prompt | Fingerprint | Run at (UTC) |
|---|---|---|---|---|
| `s1a-1.0_main_r1.results.json` | `64bf800b6422094dc7e3f14e6a3d4b243085c3c61f6750430a09f4382c4c4b46` | s1a-1.0 | `4caabb71429c` | 2026-10-07 18:10 |
| `s1a-1.1_main_r1.results.json` | `82ac8ab35be2479e6e7564f3e469d0e3789188e6615c250d9b42f6edad5e50d0` | s1a-1.1 | `952299303431` | 2026-10-07 18:42 |
| `s1a-1.2_main_r1.results.json` | `289b827f383cd65e6160fee20e80c61ff6a8419f9e81cc65d96e18382960a72d` | s1a-1.2 | `f7ec01e28167` | 2026-10-07 19:24 |

## Results

| Metric | s1a-1.0 | s1a-1.1 | s1a-1.2 |
|---|---|---|---|
| target accuracy | 0.9186 | 0.9956 | 0.9591 |
| policy accuracy | 0.9412 | 0.9956 | 0.9591 |
| target_basis accuracy | 0.9412 | 0.9956 | 0.9591 |
| stability | 0.8636 | 0.9545 | 0.9318 |
| failure rate | 0.0178 | 0.0 | 0.0222 |
| **unsafe target/policy loss (hard gate)** | **18** | **1** | **9** |
| independence failures | 0 | 0 | 0 |
| main / repair calls | 220 / 21 | 220 / 0 | 220 / 66 |
| unsafe cases | CM09, CM21, CM23, CM26, CM30 | CM30 (run 5) | CM05, CM09, CM30 |
| all gates pass | no | no | no |

## Status

- **s1a-1.0 — FAILED.** "Where" phrases had no slot after the context kinds were removed (CM09, CM10, CM19,
  CM21); `<X> experience` read as general experience (CM23, CM26, CM30); the repair message prescribed a basis
  derived from mislabelled restrictions. Corrected in s1a-1.1.
- **s1a-1.1 — BASELINE CANDIDATE, NOT AN APPROVED VERSION (superseded as the active prompt by s1a-1.3).**
  Kept runnable by explicit version under its own contract for exact replay. MAIN: the hard gate missed by
  1/225: CM30 run 5 returned `restrictions: []`, `total_experience` for "At least 6 years of project management
  experience" (its own note named the work). Diagnosed as residual run-to-run nondeterminism. This reading is
  fail-closed downstream: strict F5 makes it `target_absent_claimed` (needs confirmation, no S2 view). Pass B's
  required `target_gap` (F3 corroboration) is the designed independent second reading; strict F5 stays on until
  that is evaluated. Known remaining diagnostic: CM39 target expansion ("الائتمان بالقطاع المصرفي", the context
  attached with the proclitic ب), reported only.
- **s1a-1.2 (Option D) — WITHDRAWN.** It added one required boolean `names_role_or_work`, given before
  `targets` / `restrictions`, checked for agreement with them. Forensics of its MAIN run:
  - the model read the field as "does the statement name a job title?": `false` on 75 of 80 first answers of
    criteria without hints; the only `true` was CM38, the only one naming a role (كمحاسب); every function-only
    criterion was `false`; hinted criteria 145/145 `true`;
  - committed first, the wrong `false` either produced a consistent no-target answer the check cannot see
    (CM30 5/5, CM09 3/5, CM05 1/5 → 9 unsafe) or contradicted the restrictions (61 answers, repaired);
  - first-answer judgement accuracy without hints: 5/80; the reported `names_role_or_work_accuracy` 0.9591 only
    mirrored the restrictions after repair (it equals target accuracy);
  - implementation gap (not fixed, removed with the feature): a basis/restriction mismatch stopped validation
    before the agreement check, so the repair note never mentioned the field (CM02 4/5 failures, CM09 run 3);
  - the target-basis fixture (`s1a_target_basis_cases.json`) was never run against the real model with s1a-1.2.

  Rolled back to s1a-1.1: the runtime code (validator, repair merge, assembly, schema) is byte-identical to the
  pre-Option-D state; `prompts/s1a-1.2.txt` stays pinned (`f7ec01e2…`) and loadable only by explicit version for
  audit. Lesson for any future structured field: a judgement emitted before the evidence it summarises can bias
  that evidence, and a consistency check cannot see a wrong answer that agrees with itself.

## s1a-1.1 target-basis run (S1-A-1.1 / S1-A-1.3 review)

s1a-1.1 against the target-basis fixture (`s1a_target_basis_cases.json`, s1a-basis-1, sha256 `02a452bb…`, 44 cases,
22 without a role/work target), 5 runs, run on the VPS. Its `results.json` is NOT in this repository (not uploaded);
the figures below are the ones reported by the operator:

| Metric | s1a-1.1 |
|---|---|
| target accuracy | 0.9764 (207/212) |
| policy accuracy | 0.7783 (165/212) |
| target_basis accuracy | 0.6604 (140/212) |
| stability | 0.9091 (40/44) |
| failure rate | 0.0364 (8/220) |
| **unsafe target/policy loss (hard gate)** | **47** |

Root causes (S1-A-1.3 review, code-level; the per-run attribution needs the results file):
- the prompt told the model to ignore exactly the words that distinguish `setting_only` from `total_experience`
  ("Decide the basis from the role and work words ONLY. Words that say where or for whom never change it";
  setting_only "Do not quote or describe that limit"; total_experience no longer excluded a where limit;
  "experience in <X>" always named work, "never restrictions []");
- `setting_only` was the only basis with no evidence slot: `[]` + `total_experience` and a where phrase typed
  `vague` + `unspecified` were both valid answers and both unsafe (sector -> pure_duration); the arithmetic of
  the reported totals bounds the setting_only downgrades at >= 42 of 50 runs;
- the repair could never reach a correct setting_only once the main answer typed the where phrase (restriction
  locks + F6); its only "successful" repair turned the workplace into a function (BA16);
- `vague` had been dropped from the definition of a restriction ("names WHAT"), so relevant/related answers
  slid to `total_experience`; the Arabic adjective form ("خبرة إدارية") had no rule (BA22).

- **s1a-1.3 — FAILED STAGE B (superseded as the active prompt by s1a-1.4); kept runnable for exact replay.**
  Prompt `prompts/s1a-1.3.txt` (sha256 `0cf68cadc53d05e8e26c75bb94d2ea279f91dcbb65d8663f17b9052f4c98656d`):
  WHAT before WHERE; X in "experience in <X>" is work only when it is an activity / discipline / field of work;
  a place / sector / industry / kind of employer or client is a where limit, quoted verbatim in the new
  `where_evidence` field exactly when it is the only limit (`setting_only`); vague restored; total_experience
  limits neither work nor where; the Arabic "خبرة" + adjective rule; one setting_only and one unspecified
  OUTPUT example (wording outside every fixture). Contract `pass_a.CONTRACTS["s1a-1.3"]`: where_evidence
  required exactly for setting_only (structural checks only), the basis derived from the raw typed answer, the
  neutral four-way repair message, where_evidence follows the basis in the merge, and the where guard (a repair
  never turns a main where_evidence span into a role / function). F6 and the restriction locks unchanged.
  Evaluation fixture `s1a_target_basis_cases_v2.json` (s1a-basis-2, sha256 `2daadcb1…`): same cases and gold,
  where_evidence added to the 10 setting_only oracle answers. Offline: oracle 1.0 on MAIN and s1a-basis-2; the
  recorded s1a-1.1 MAIN runs replay exactly (220/220) under the s1a-1.1 contract.
  Unsafe readings that structural validation cannot prevent (a self-consistent answer): a where-only reading
  that omits a named role / work (`setting_only` + evidence, or `[]` + `total_experience`); a sector phrase typed
  as a function (narrowing, reported as `sector_to_function`); an adjective function read as general experience.
  They are measured by the unchanged unsafe gate and blocked downstream by strict F5 (no S2 view).

## s1a-1.3 target-basis Stage B (2026-10-08)

Run on the VPS (`/root/s1a13_target_basis_r1_20261008T184108Z/results.json`), preserved byte for byte as
`s1a-1.3_target_basis_r1.results.json` (sha256 `3524eb85b94dbb403ba5214a6b844e22ec8f98396ff30315e54ca01f9fe0f6d9`,
SYNTHETIC JDs and model outputs only). Its 220 recorded raw answers replay exactly (observations, repairs, summary and
gates identical) under the s1a-1.3 contract and under the current one (`tests/test_s1a13_stage_b_replay.py`).

| Metric | s1a-1.1 | s1a-1.3 |
|---|---|---|
| target accuracy | 0.9764 | 0.8630 (189/219) |
| policy accuracy | 0.7783 | 0.8402 (184/219) |
| target_basis accuracy | 0.6604 | 0.7717 (169/219) |
| stability | 0.9091 | 0.9091 |
| failures | 8 | 1 (BA16 run 1, fail closed) |
| **unsafe loss** | **47** | **35** |

Every wrong answer (50) was a FIRST answer, accepted without repair: `restrictions: []` + `total_experience`.
30 runs lost a named function (BE12, BE14, BE17, BE21, BA22 5/5; BA11 4/5; BA21 1/5) although the model's own note
named the work in all 30; BE09 ("experience in schools") 5/5; the vague BE05 / BE06 / BA06 15/15 (not unsafe).
Setting_only otherwise recovered (44/49 valid runs, all with where_evidence; 0 sector -> function). The 9 repairs
ended correct 8 times; the 9th failed closed. Diagnosis: total_experience acted as the evidence-free default once
s1a-1.1's "a statement naming work is never restrictions [] / always targets" rules were removed; the notes read
the label as "the total number of years".

- **s1a-1.4 — CURRENT ACTIVE PROMPT. CANDIDATE PENDING REAL EVALUATION (Stage B), NOT AN APPROVED VERSION.**
  Prompt only (`prompts/s1a-1.4.txt`, sha256 `1cc53afc9e79e2137ed85c5569f9a07a348350367153ed0396190dbe58613de3`);
  the s1a-1.3 contract, validator, repair and harness gates unchanged. Changes: a statement naming work or a role is
  never `restrictions: []` / `total_experience`; `total_experience` is only genuinely unrestricted experience and the
  duration never decides the basis (a re-read check before answering it); a relevance word is always a vague
  restriction and never removes named work; the Arabic adjective rule adds work / responsibility adjectives,
  relevance adjectives (vague) and an "if unsure, function + ambiguous_relevance" default; descriptive wording
  ("how long" removed) never removes named work; a kind of workplace or employer is a where limit without the word
  sector / industry. New examples avoid every evaluation and held-out fixture wording (test-checked).

## Next evaluations (not yet run; each only after the previous one passes)

Stage B — s1a-1.4 against the target-basis fixture (s1a-basis-2), every gate including zero unsafe loss:

```
python3 scripts/s1_pass_a_eval.py --out <dir>/s1a14_basis --fixture target_basis --mode real --runs 5 --confirm-real
```

Stage C — MAIN (only if Stage B passes): no metric below the s1a-1.1 MAIN baseline (0.9956 / 0.9956 / 0.9956,
stability 0.9545) and zero unsafe loss:

```
python3 scripts/s1_pass_a_eval.py --out <dir>/s1a14_main --fixture main --mode real --runs 5 --confirm-real
```

Stage D — the S1-A-1.3 target-basis HELD-OUT set (only if Stage C passes; run once; never used for tuning):
`s1a_target_basis_heldout_cases.json` (s1a-basis-heldout-1, sha256
`81e607d183a186bf1d153a534bdd92898a7cb247f5ad3378cf4b109fc95f7018`), 52 cases (26 English / 26 Arabic):
functional, role, setting-only, combined function/role + where, unrestricted, vague, ambiguous wording, adjective
forms. Written after prompt s1a-1.3 was fixed and committed (8ef8e4c), expected classifications frozen before any
run; the harness refuses a real run without `--allow-heldout`. Report its results independently of Stages B / C.

```
python3 scripts/s1_pass_a_eval.py --out <dir>/s1a14_heldout --fixture target_basis_heldout --mode real --runs 5 --confirm-real --allow-heldout
```
