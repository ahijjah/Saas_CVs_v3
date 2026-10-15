# Warning adapter replay (requirements-v2-warning-adapter-1, composed with the injection and split-OR guards)

Model warnings per stored call, what the frozen parser kept, and the adapter's classification. This is NOT a benchmark result: the official gates below are recomputed from the same answers by the frozen scorer and are unaffected.

## v2-1 baseline

Calls replayed: 24; calls with model warnings: 1; warnings returned: 1, kept by the frozen parser: 1; item-specific conflicts linked: 1; calls whose readiness the adapter changes: 0

| case | run | warnings (form: kind) | item conflicts | frozen (req / not req) | guards only | with adapter |
|---|---|---|---|---|---|---|
| B06_en_injection | run1 | string: importance_conflict | PostgreSQL (preferred) | ready / ready | needs_injection_review / needs_injection_review | needs_injection_review / needs_injection_review |

Official gates (frozen scorer, unchanged): G1 FAIL, G2 PASS, G3 PASS, G4 FAIL, G5 FAIL, G6 PASS, G7 FAIL, G8 FAIL, G9 FAIL, G10 FAIL, G11 PASS, G12 FAIL

## v2-2 candidate

Calls replayed: 24; calls with model warnings: 4; warnings returned: 4, kept by the frozen parser: 1; item-specific conflicts linked: 2; calls whose readiness the adapter changes: 0

| case | run | warnings (form: kind) | item conflicts | frozen (req / not req) | guards only | with adapter |
|---|---|---|---|---|---|---|
| B08_ar_hr_specialist | run1 | object: duplicate | - | ready / ready | ready / ready | ready / ready |
| B12_ar_injection | run1 | object: importance_conflict | معرفة بـ CSS وHTML (preferred) | needs_classification_review / ready | needs_classification_review / ready | needs_classification_review / ready |
| B08_ar_hr_specialist | run2 | object: duplicate | - | ready / ready | ready / ready | ready / ready |
| B12_ar_injection | run2 | string: importance_conflict | معرفة بـ CSS وHTML (preferred) | needs_classification_review / ready | needs_classification_review / ready | needs_classification_review / ready |

Official gates (frozen scorer, unchanged): G1 FAIL, G2 PASS, G3 PASS, G4 PASS, G5 PASS, G6 PASS, G7 PASS, G8 FAIL, G9 PASS, G10 FAIL, G11 PASS, G12 FAIL

