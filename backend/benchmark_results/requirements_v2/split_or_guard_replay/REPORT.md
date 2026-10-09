# Split-OR guard replay (requirements-v2-split-or-guard-1, composed with the injection guard)

Readiness per stored call: frozen parser, with the injection guard only, and composed with the split-OR guard. This is NOT a benchmark result: the official gates below are recomputed from the same answers by the frozen scorer and are unaffected by either guard.

## v2-1 baseline

Calls replayed: 24; calls with a split-OR group: 7; readiness changed by the split-OR guard (beyond the injection guard): 5

| case | run | form | options | frozen (ack req / not req) | injection guard only | composed (both policies) |
|---|---|---|---|---|---|---|
| B06_en_injection | run1 | single_entry_mutual | Python / Java | ready / ready | needs_injection_review / needs_injection_review | needs_injection_review / needs_injection_review |
| B08_ar_hr_specialist | run1 | single_entry_mutual | اللغة العربية / اللغة الإنجليزية | ready / ready | ready / ready | needs_split_or_review / needs_split_or_review |
| B12_ar_injection | run1 | single_entry_mutual | React / Vue | ready / ready | ready / ready | needs_split_or_review / needs_split_or_review |
| B02_en_data_analyst | run2 | single_entry_mutual | Python / R | ready / ready | ready / ready | needs_split_or_review / needs_split_or_review |
| B06_en_injection | run2 | single_entry_mutual | Python / Java | ready / ready | needs_injection_review / needs_injection_review | needs_injection_review / needs_injection_review |
| B08_ar_hr_specialist | run2 | single_entry_mutual | اللغة العربية / اللغة الإنجليزية | ready / ready | ready / ready | needs_split_or_review / needs_split_or_review |
| B12_ar_injection | run2 | single_entry_mutual | React / Vue | needs_classification_review / ready | needs_classification_review / ready | needs_split_or_review / needs_split_or_review |

Official gates (frozen scorer, unchanged by the guards): G1 FAIL, G2 PASS, G3 PASS, G4 FAIL, G5 FAIL, G6 PASS, G7 FAIL, G8 FAIL, G9 FAIL, G10 FAIL, G11 PASS, G12 FAIL

## v2-2 candidate

Calls replayed: 24; calls with a split-OR group: 4; readiness changed by the split-OR guard (beyond the injection guard): 3

| case | run | form | options | frozen (ack req / not req) | injection guard only | composed (both policies) |
|---|---|---|---|---|---|---|
| B02_en_data_analyst | run1 | complete_alternatives_repeated | Python / R | ready / ready | ready / ready | needs_split_or_review / needs_split_or_review |
| B06_en_injection | run1 | complete_alternatives_repeated | Python / Java | needs_classification_review / ready | needs_injection_review / needs_injection_review | needs_injection_review / needs_injection_review |
| B02_en_data_analyst | run2 | complete_alternatives_repeated | Python / R | ready / ready | ready / ready | needs_split_or_review / needs_split_or_review |
| B06_en_injection | run2 | complete_alternatives_repeated | Python / Java | needs_classification_review / ready | needs_classification_review / ready | needs_split_or_review / needs_split_or_review |

Official gates (frozen scorer, unchanged by the guards): G1 FAIL, G2 PASS, G3 PASS, G4 PASS, G5 PASS, G6 PASS, G7 PASS, G8 FAIL, G9 PASS, G10 FAIL, G11 PASS, G12 FAIL

