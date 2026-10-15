# Experimental scorer omission-reference-experimental-scorer-v1

Replay of stored answers only. Experimental: not production scoring, not candidate evaluation, and not a change to the frozen scorer.

| group | JD | source-unit omissions | alternatives (RQ-027) | independent items (RQ-032) | child routing | importance (CSS/HTML) |
|---|---|---|---|---|---|---|
| TC20 repeat · arm v2-3 · call 1 | TC20 | 2 | n/a | n/a | n/a | n/a |
| TC20 repeat · arm v2-5 · call 1 | TC20 | 3 | n/a | n/a | n/a | n/a |
| TC20 repeat · arm v2-3 · call 2 | TC20 | 2 | n/a | n/a | n/a | n/a |
| TC20 repeat · arm v2-5 · call 2 | TC20 | 3 | n/a | n/a | n/a | n/a |
| TC20 repeat · arm v2-3 · call 3 | TC20 | 2 | n/a | n/a | n/a | n/a |
| TC20 repeat · arm v2-5 · call 3 | TC20 | 3 | n/a | n/a | n/a | n/a |
| TC20 repeat · arm v2-3 · call 4 | TC20 | 2 | n/a | n/a | n/a | n/a |
| TC20 repeat · arm v2-5 · call 4 | TC20 | 3 | n/a | n/a | n/a | n/a |
| TC20 repeat · arm v2-3 · call 5 | TC20 | 18 | n/a | n/a | n/a | n/a |
| TC20 repeat · arm v2-5 · call 5 | TC20 | 3 | n/a | n/a | n/a | n/a |
| TC20 audit · arm v2-3 · call 1 | TC20 | 22 | n/a | n/a | n/a | n/a |
| TC20 audit · arm v2-3 · call 2 | TC20 | 20 | n/a | n/a | n/a | n/a |
| TC20 audit · arm v2-3 · call 3 | TC20 | 20 | n/a | n/a | n/a | n/a |
| TC20 audit · arm v2-3 · call 4 | TC20 | 22 | n/a | n/a | n/a | n/a |
| TC20 audit · arm v2-3 · call 5 | TC20 | 20 | n/a | n/a | n/a | n/a |
| TC20 audit · arm v2-4 · call 1 | TC20 | 21 | n/a | n/a | n/a | n/a |
| TC20 audit · arm v2-4 · call 2 | TC20 | 20 | n/a | n/a | n/a | n/a |
| TC20 audit · arm v2-4 · call 3 | TC20 | 21 | n/a | n/a | n/a | n/a |
| TC20 audit · arm v2-4 · call 4 | TC20 | 20 | n/a | n/a | n/a | n/a |
| TC20 audit · arm v2-4 · call 5 | TC20 | 20 | n/a | n/a | n/a | n/a |
| benchmark run dir v2-2_run1 · run run1 | B02_en_data_analyst | 0 | split | n/a | n/a | n/a |
| benchmark run dir v2-2_run1 · run run1 | B03_en_warehouse_supervisor | 1 | n/a | n/a | C01=partial, C02=met, C03=met, C04=misrouted, C05=met | n/a |
| benchmark run dir v2-2_run1 · run run1 | B07_ar_accountant | 0 | n/a | n/a | C01=partial, C02=met, C03=misrouted | n/a |
| benchmark run dir v2-2_run1 · run run1 | B08_ar_hr_specialist | 0 | n/a | n/a | n/a | n/a |
| benchmark run dir v2-2_run1 · run run1 | B12_ar_injection | 0 | n/a | RQ-032=incorrectly merged | n/a | {'CSS': ['preferred'], 'HTML': ['preferred']} within accepted: True; schema findings: 0 |
| benchmark run dir v2-2_run1 · run run2 | B02_en_data_analyst | 0 | split | n/a | n/a | n/a |
| benchmark run dir v2-2_run1 · run run2 | B03_en_warehouse_supervisor | 1 | n/a | n/a | C01=met, C02=met, C03=met, C04=misrouted, C05=misrouted | n/a |
| benchmark run dir v2-2_run1 · run run2 | B07_ar_accountant | 2 | n/a | n/a | C01=partial, C02=met, C03=misrouted | n/a |
| benchmark run dir v2-2_run1 · run run2 | B08_ar_hr_specialist | 0 | n/a | n/a | n/a | n/a |
| benchmark run dir v2-2_run1 · run run2 | B12_ar_injection | 0 | n/a | RQ-032=incorrectly merged | n/a | {'CSS': ['preferred'], 'HTML': ['preferred']} within accepted: True; schema findings: 0 |
| benchmark run dir baseline_v2-1 · run run1 | B02_en_data_analyst | 0 | incomplete | n/a | n/a | n/a |
| benchmark run dir baseline_v2-1 · run run1 | B03_en_warehouse_supervisor | 3 | n/a | n/a | C01=met, C02=missing, C03=misrouted, C04=met, C05=met | n/a |
| benchmark run dir baseline_v2-1 · run run1 | B07_ar_accountant | 2 | n/a | n/a | C01=missing, C02=misrouted, C03=met | n/a |
| benchmark run dir baseline_v2-1 · run run1 | B08_ar_hr_specialist | 0 | n/a | n/a | n/a | n/a |
| benchmark run dir baseline_v2-1 · run run1 | B12_ar_injection | 2 | n/a | RQ-032=missing: CSS, HTML | n/a | {'CSS': ['preferred'], 'HTML': ['preferred']} within accepted: True; schema findings: 0 |
| benchmark run dir baseline_v2-1 · run run2 | B02_en_data_analyst | 0 | split | n/a | n/a | n/a |
| benchmark run dir baseline_v2-1 · run run2 | B03_en_warehouse_supervisor | 1 | n/a | n/a | C01=partial, C02=missing, C03=met, C04=met, C05=met | n/a |
| benchmark run dir baseline_v2-1 · run run2 | B07_ar_accountant | 2 | n/a | n/a | C01=missing, C02=misrouted, C03=met | n/a |
| benchmark run dir baseline_v2-1 · run run2 | B08_ar_hr_specialist | 2 | n/a | n/a | n/a | n/a |
| benchmark run dir baseline_v2-1 · run run2 | B12_ar_injection | 1 | n/a | RQ-032=correctly split | n/a | {'CSS': ['preferred'], 'HTML': ['preferred']} within accepted: True; schema findings: 0 |

Conflict evidence (review evidence, not used in importance):
- RQ-021: ملاحظة من مسؤول التوظيف: معرفة CSS وHTML اختيارية لهذه الوظيفة. (against unit line 945b2154c3f2:L6:101-122)

## Comparison with the frozen benchmark scorer (current behaviour on the same stored answers)

| defect | frozen scorer output | experimental output |
|---|---|---|
| S002 Python or R, one item with alternatives | benchmark run dir v2-2_run1 run run1 B02: frozen alternatives errors 0 | benchmark run dir v2-2_run1 run run1 B02: split; recorded [('Python', ['Python', 'R']), ('R', ['Python', 'R'])] |
| S010 / RQ-029 child routing | benchmark run dir v2-2_run1 run run1 B03: conditions routed 4 of 5 | benchmark run dir v2-2_run1 run run1 B03: C01=partial, C02=met, C03=met, C04=misrouted, C05=met |
| S008 CSS and HTML, two items | benchmark run dir v2-2_run1 run run1 B12: merged 0, missing ['HTML'] | benchmark run dir v2-2_run1 run run1 B12: incorrectly merged; item sources CSS←required_unit, HTML←required_unit |
| RQ-021 conflict and importance | benchmark run dir v2-2_run1 run run1 B12: conflict surfaced 1 (in the label) | benchmark run dir v2-2_run1 run run1 B12: importance {'CSS': ['preferred'], 'HTML': ['preferred']}; evidence kept separately |
| S002 Python or R, one item with alternatives | benchmark run dir v2-2_run1 run run2 B02: frozen alternatives errors 0 | benchmark run dir v2-2_run1 run run2 B02: split; recorded [('Python', ['Python', 'R']), ('R', ['Python', 'R'])] |
| S010 / RQ-029 child routing | benchmark run dir v2-2_run1 run run2 B03: conditions routed 3 of 5 | benchmark run dir v2-2_run1 run run2 B03: C01=met, C02=met, C03=met, C04=misrouted, C05=misrouted |
| S008 CSS and HTML, two items | benchmark run dir v2-2_run1 run run2 B12: merged 0, missing ['HTML'] | benchmark run dir v2-2_run1 run run2 B12: incorrectly merged; item sources CSS←required_unit, HTML←required_unit |
| RQ-021 conflict and importance | benchmark run dir v2-2_run1 run run2 B12: conflict surfaced 1 (in the label) | benchmark run dir v2-2_run1 run run2 B12: importance {'CSS': ['preferred'], 'HTML': ['preferred']}; evidence kept separately |
| S002 Python or R, one item with alternatives | benchmark run dir baseline_v2-1 run run1 B02: frozen alternatives errors 1 | benchmark run dir baseline_v2-1 run run1 B02: incomplete; recorded [('Python', ['R'])] |
| S010 / RQ-029 child routing | benchmark run dir baseline_v2-1 run run1 B03: conditions routed 3 of 5 | benchmark run dir baseline_v2-1 run run1 B03: C01=met, C02=missing, C03=misrouted, C04=met, C05=met |
| S008 CSS and HTML, two items | benchmark run dir baseline_v2-1 run run1 B12: merged 0, missing ['CSS', 'HTML', 'خبرة في TypeScript'] | benchmark run dir baseline_v2-1 run run1 B12: missing: CSS, HTML; item sources CSS←conflict_note, HTML←conflict_note |
| RQ-021 conflict and importance | benchmark run dir baseline_v2-1 run run1 B12: conflict surfaced 0 (in the label) | benchmark run dir baseline_v2-1 run run1 B12: importance {'CSS': ['preferred'], 'HTML': ['preferred']}; evidence kept separately |
| S002 Python or R, one item with alternatives | benchmark run dir baseline_v2-1 run run2 B02: frozen alternatives errors 1 | benchmark run dir baseline_v2-1 run run2 B02: split; recorded [('Python', ['R']), ('R', ['Python'])] |
| S010 / RQ-029 child routing | benchmark run dir baseline_v2-1 run run2 B03: conditions routed 4 of 5 | benchmark run dir baseline_v2-1 run run2 B03: C01=partial, C02=missing, C03=met, C04=met, C05=met |
| S008 CSS and HTML, two items | benchmark run dir baseline_v2-1 run run2 B12: merged 0, missing ['خبرة في TypeScript'] | benchmark run dir baseline_v2-1 run run2 B12: correctly split; item sources CSS←required_unit, HTML←required_unit |
| RQ-021 conflict and importance | benchmark run dir baseline_v2-1 run run2 B12: conflict surfaced 2 (in the label) | benchmark run dir baseline_v2-1 run run2 B12: importance {'CSS': ['preferred'], 'HTML': ['preferred']}; evidence kept separately |
