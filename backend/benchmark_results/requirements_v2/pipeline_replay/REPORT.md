# Pipeline replay (requirements-v2-pipeline-1)

Candidate readiness and unresolved issues for every stored response (48 = 2 prompts x 24 calls; each prompt's 24 calls are the 12 job descriptions x run1 + run2), reported under BOTH classification-policy settings. This is a readiness report, NOT a benchmark result: the official gates (last line of each section) are recomputed from the same answers by the frozen scorer, compared with the saved `results.json`, and are unchanged. 'Newly blocked' = the frozen readiness alone says `ready`, the pipeline does not.

## v2-1 baseline

Calls replayed: 24 = both runs (run1 + run2) of the 12 job descriptions (unusable skipped: 0); parsed: 24; contract problems: 0; raw/original/draft not intact: 0

**Policy Yes (acknowledgment required)** — pipeline states: {'ready': 8, 'needs_classification_review': 3, 'needs_items': 6, 'needs_injection_review': 2, 'needs_split_or_review': 5}; frozen-only states: {'ready': 14, 'needs_classification_review': 4, 'needs_items': 6}; frozen ready but pipeline blocked: 6; blocking issues by kind: {'classification:preferred_cue_not_linked_to_item': 9, 'no_items:no_items': 6, 'injection:injection_weights': 2, 'split_or:split_or_requirement': 7, 'conflict:model_importance_conflict': 1, 'confirmation:preferred_only_unconfirmed': 2}

**Policy No (acknowledgment not required)** — pipeline states: {'ready': 9, 'needs_items': 6, 'needs_injection_review': 2, 'needs_split_or_review': 5, 'needs_confirmation': 2}; frozen-only states: {'ready': 16, 'needs_items': 6, 'needs_confirmation': 2}; frozen ready but pipeline blocked: 7; blocking issues by kind: {'no_items:no_items': 6, 'injection:injection_weights': 2, 'split_or:split_or_requirement': 7, 'confirmation:preferred_only_unconfirmed': 2}

| case | run | pipeline (Yes / No) | frozen only (Yes / No) | blocking issues (Yes) | blocking issues (No) | visible only (No) | warnings | similarity |
|---|---|---|---|---|---|---|---|---|
| B01_en_hr_manager | run1 | ready / ready | ready / ready | - | - | - | 0 | 0 |
| B02_en_data_analyst | run1 | ready / ready | ready / ready | - | - | - | 0 | 0 |
| B03_en_warehouse_supervisor | run1 | needs_classification_review / ready | needs_classification_review / ready | classification:preferred_cue_not_linked_to_item | - | classification:preferred_cue_not_linked_to_item | 0 | 0 |
| B04_en_preferred_only | run1 | needs_items / needs_items | needs_items / needs_items | no_items:no_items | no_items:no_items | - | 0 | 0 |
| B05_en_open_empty | run1 | needs_items / needs_items | needs_items / needs_items | no_items:no_items | no_items:no_items | - | 0 | 0 |
| B06_en_injection | run1 | needs_injection_review / needs_injection_review | ready / ready | injection:injection_weights; split_or:split_or_requirement; conflict:model_importance_conflict | injection:injection_weights; split_or:split_or_requirement | conflict:model_importance_conflict | 1 | 0 |
| B07_ar_accountant | run1 | ready / ready | ready / ready | - | - | - | 0 | 0 |
| B08_ar_hr_specialist | run1 | needs_split_or_review / needs_split_or_review | ready / ready | split_or:split_or_requirement | split_or:split_or_requirement | - | 0 | 0 |
| B09_ar_sales_rep | run1 | ready / ready | ready / ready | - | - | - | 0 | 0 |
| B10_ar_preferred_only | run1 | needs_classification_review / needs_confirmation | needs_classification_review / needs_confirmation | classification:preferred_cue_not_linked_to_item; classification:preferred_cue_not_linked_to_item; classification:preferred_cue_not_linked_to_item; confirmation:preferred_only_unconfirmed | confirmation:preferred_only_unconfirmed | classification:preferred_cue_not_linked_to_item; classification:preferred_cue_not_linked_to_item; classification:preferred_cue_not_linked_to_item | 0 | 0 |
| B11_ar_open_empty | run1 | needs_items / needs_items | needs_items / needs_items | no_items:no_items | no_items:no_items | - | 0 | 0 |
| B12_ar_injection | run1 | needs_split_or_review / needs_split_or_review | ready / ready | split_or:split_or_requirement | split_or:split_or_requirement | - | 0 | 0 |
| B01_en_hr_manager | run2 | ready / ready | ready / ready | - | - | - | 0 | 0 |
| B02_en_data_analyst | run2 | needs_split_or_review / needs_split_or_review | ready / ready | split_or:split_or_requirement | split_or:split_or_requirement | - | 0 | 0 |
| B03_en_warehouse_supervisor | run2 | ready / ready | ready / ready | - | - | - | 0 | 0 |
| B04_en_preferred_only | run2 | needs_items / needs_items | needs_items / needs_items | no_items:no_items | no_items:no_items | - | 0 | 0 |
| B05_en_open_empty | run2 | needs_items / needs_items | needs_items / needs_items | no_items:no_items | no_items:no_items | - | 0 | 0 |
| B06_en_injection | run2 | needs_injection_review / needs_injection_review | ready / ready | injection:injection_weights; split_or:split_or_requirement | injection:injection_weights; split_or:split_or_requirement | - | 0 | 0 |
| B07_ar_accountant | run2 | ready / ready | ready / ready | - | - | - | 0 | 0 |
| B08_ar_hr_specialist | run2 | needs_split_or_review / needs_split_or_review | ready / ready | split_or:split_or_requirement | split_or:split_or_requirement | - | 0 | 0 |
| B09_ar_sales_rep | run2 | ready / ready | ready / ready | - | - | - | 0 | 0 |
| B10_ar_preferred_only | run2 | needs_classification_review / needs_confirmation | needs_classification_review / needs_confirmation | classification:preferred_cue_not_linked_to_item; classification:preferred_cue_not_linked_to_item; classification:preferred_cue_not_linked_to_item; confirmation:preferred_only_unconfirmed | confirmation:preferred_only_unconfirmed | classification:preferred_cue_not_linked_to_item; classification:preferred_cue_not_linked_to_item; classification:preferred_cue_not_linked_to_item | 0 | 0 |
| B11_ar_open_empty | run2 | needs_items / needs_items | needs_items / needs_items | no_items:no_items | no_items:no_items | - | 0 | 0 |
| B12_ar_injection | run2 | needs_split_or_review / needs_split_or_review | needs_classification_review / ready | split_or:split_or_requirement; classification:preferred_cue_not_linked_to_item; classification:preferred_cue_not_linked_to_item | split_or:split_or_requirement | classification:preferred_cue_not_linked_to_item; classification:preferred_cue_not_linked_to_item | 0 | 0 |

Official gates (frozen scorer, recomputed): G1 FAIL, G2 PASS, G3 PASS, G4 FAIL, G5 FAIL, G6 PASS, G7 FAIL, G8 FAIL, G9 FAIL, G10 FAIL, G11 PASS, G12 FAIL
Identical to the saved results.json: yes

## v2-2 candidate

Calls replayed: 24 = both runs (run1 + run2) of the 12 job descriptions (unusable skipped: 0); parsed: 24; contract problems: 0; raw/original/draft not intact: 0

**Policy Yes (acknowledgment required)** — pipeline states: {'ready': 10, 'needs_split_or_review': 3, 'needs_confirmation': 4, 'needs_items': 4, 'needs_injection_review': 1, 'needs_classification_review': 2}; frozen-only states: {'ready': 12, 'needs_confirmation': 4, 'needs_items': 4, 'needs_classification_review': 4}; frozen ready but pipeline blocked: 2; blocking issues by kind: {'split_or:split_or_requirement': 4, 'confirmation:preferred_only_unconfirmed': 4, 'no_items:no_items': 4, 'injection:injection_requirement': 1, 'injection:injection_weights': 1, 'classification:preferred_cue_not_linked_to_item': 4, 'classification:preferred_cue_missing': 1, 'conflict:model_importance_conflict': 2}

**Policy No (acknowledgment not required)** — pipeline states: {'ready': 12, 'needs_split_or_review': 3, 'needs_confirmation': 4, 'needs_items': 4, 'needs_injection_review': 1}; frozen-only states: {'ready': 16, 'needs_confirmation': 4, 'needs_items': 4}; frozen ready but pipeline blocked: 4; blocking issues by kind: {'split_or:split_or_requirement': 4, 'confirmation:preferred_only_unconfirmed': 4, 'no_items:no_items': 4, 'injection:injection_requirement': 1, 'injection:injection_weights': 1}

| case | run | pipeline (Yes / No) | frozen only (Yes / No) | blocking issues (Yes) | blocking issues (No) | visible only (No) | warnings | similarity |
|---|---|---|---|---|---|---|---|---|
| B01_en_hr_manager | run1 | ready / ready | ready / ready | - | - | - | 0 | 0 |
| B02_en_data_analyst | run1 | needs_split_or_review / needs_split_or_review | ready / ready | split_or:split_or_requirement | split_or:split_or_requirement | - | 0 | 0 |
| B03_en_warehouse_supervisor | run1 | ready / ready | ready / ready | - | - | - | 0 | 0 |
| B04_en_preferred_only | run1 | needs_confirmation / needs_confirmation | needs_confirmation / needs_confirmation | confirmation:preferred_only_unconfirmed | confirmation:preferred_only_unconfirmed | - | 0 | 0 |
| B05_en_open_empty | run1 | needs_items / needs_items | needs_items / needs_items | no_items:no_items | no_items:no_items | - | 0 | 0 |
| B06_en_injection | run1 | needs_injection_review / needs_injection_review | needs_classification_review / ready | injection:injection_requirement; injection:injection_weights; split_or:split_or_requirement; classification:preferred_cue_not_linked_to_item; classification:preferred_cue_missing | injection:injection_requirement; injection:injection_weights; split_or:split_or_requirement | classification:preferred_cue_not_linked_to_item; classification:preferred_cue_missing | 0 | 1 |
| B07_ar_accountant | run1 | ready / ready | ready / ready | - | - | - | 0 | 0 |
| B08_ar_hr_specialist | run1 | ready / ready | ready / ready | - | - | - | 1 | 0 |
| B09_ar_sales_rep | run1 | ready / ready | ready / ready | - | - | - | 0 | 0 |
| B10_ar_preferred_only | run1 | needs_confirmation / needs_confirmation | needs_confirmation / needs_confirmation | confirmation:preferred_only_unconfirmed | confirmation:preferred_only_unconfirmed | - | 0 | 0 |
| B11_ar_open_empty | run1 | needs_items / needs_items | needs_items / needs_items | no_items:no_items | no_items:no_items | - | 0 | 0 |
| B12_ar_injection | run1 | needs_classification_review / ready | needs_classification_review / ready | classification:preferred_cue_not_linked_to_item; conflict:model_importance_conflict | - | classification:preferred_cue_not_linked_to_item; conflict:model_importance_conflict | 1 | 0 |
| B01_en_hr_manager | run2 | ready / ready | ready / ready | - | - | - | 0 | 0 |
| B02_en_data_analyst | run2 | needs_split_or_review / needs_split_or_review | ready / ready | split_or:split_or_requirement | split_or:split_or_requirement | - | 0 | 0 |
| B03_en_warehouse_supervisor | run2 | ready / ready | ready / ready | - | - | - | 0 | 0 |
| B04_en_preferred_only | run2 | needs_confirmation / needs_confirmation | needs_confirmation / needs_confirmation | confirmation:preferred_only_unconfirmed | confirmation:preferred_only_unconfirmed | - | 0 | 0 |
| B05_en_open_empty | run2 | needs_items / needs_items | needs_items / needs_items | no_items:no_items | no_items:no_items | - | 0 | 0 |
| B06_en_injection | run2 | needs_split_or_review / needs_split_or_review | needs_classification_review / ready | split_or:split_or_requirement; classification:preferred_cue_not_linked_to_item | split_or:split_or_requirement | classification:preferred_cue_not_linked_to_item | 0 | 0 |
| B07_ar_accountant | run2 | ready / ready | ready / ready | - | - | - | 0 | 0 |
| B08_ar_hr_specialist | run2 | ready / ready | ready / ready | - | - | - | 1 | 0 |
| B09_ar_sales_rep | run2 | ready / ready | ready / ready | - | - | - | 0 | 0 |
| B10_ar_preferred_only | run2 | needs_confirmation / needs_confirmation | needs_confirmation / needs_confirmation | confirmation:preferred_only_unconfirmed | confirmation:preferred_only_unconfirmed | - | 0 | 0 |
| B11_ar_open_empty | run2 | needs_items / needs_items | needs_items / needs_items | no_items:no_items | no_items:no_items | - | 0 | 0 |
| B12_ar_injection | run2 | needs_classification_review / ready | needs_classification_review / ready | classification:preferred_cue_not_linked_to_item; conflict:model_importance_conflict | - | classification:preferred_cue_not_linked_to_item; conflict:model_importance_conflict | 1 | 0 |

Official gates (frozen scorer, recomputed): G1 FAIL, G2 PASS, G3 PASS, G4 PASS, G5 PASS, G6 PASS, G7 PASS, G8 FAIL, G9 PASS, G10 FAIL, G11 PASS, G12 FAIL
Identical to the saved results.json: yes

