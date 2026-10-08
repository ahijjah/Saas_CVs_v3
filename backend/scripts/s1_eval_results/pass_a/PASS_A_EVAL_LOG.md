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
- **s1a-1.1 — CURRENT ACTIVE PROMPT. BASELINE CANDIDATE, NOT AN APPROVED VERSION.** The hard gate missed by
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

## Next evaluation (not yet run)

s1a-1.1 against the target-basis fixture (44 cases, 22 without a role/work target), the first measurement of
over-targeting on legitimate total-experience / setting-only statements that MAIN cannot measure:

```
python3 scripts/s1_pass_a_eval.py --out <dir>/s1a11_basis --fixture target_basis --mode real --runs 5 --confirm-real
```
