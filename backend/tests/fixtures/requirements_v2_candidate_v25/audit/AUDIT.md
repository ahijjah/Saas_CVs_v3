# Audit: the completed v2-3 versus v2-5 comparison (gpt-4.1-2025-04-14, 22 calls)

Status: **provenance verified; the saved report reproduced exactly; the flags traced to their rules and classified.**
This audit made no paid call and changed no prompt, label, scorer, frozen artifact or production setting.
Evidence: `run_v2-3_v2-5_20261010/` (the uploads, byte for byte, plus `audit.json` and `summary.json`, written by
`scripts/requirements_v2_candidate_audit.py --write`). Commit of the run: `1e73231bda9e1061d5d78be29eb68bf3c1154e4b`.

The directory named in the request (`/root/req_v2_v25_comparison_20261010`) does not exist on this machine; the three uploads
(`1179a933-scored.json`, `278065f0-manifest.json`, `1e1fcb97-calls.jsonl`) are the source.

## 1. Provenance and reproduction

| File | sha256 |
|---|---|
| `calls.jsonl` (22 rows) | `7a176b8ba6b78f1285187c1b72713b93aea76d2250f6b22e9b53e985c5df6f73` |
| `manifest.json` | `156924f9c21fa804c9cbdb9e9bc648a808132475d15f68156f0cbf9fe34dfea5` |
| `scored.json` | `a9fd75f6b7baacb0bcbbb50fb95cbfd4e9f7b6aaff39085bec91a3512c8d3458` |

- Pins: the manifest's candidate, control, labels, eval-set, run-script, TC20-scorer and compare-script hashes equal the current files; the git commit is `1e73231…`.
- Rows: each row's system hash equals its arm's prompt (v2-3 `21a2f942…`, v2-5 `f1569a8b…`), each row's user hash equals the recomputed user message for its JD, every row returned `gpt-4.1-2025-04-14` with `finish_reason: stop` and no error, and every row's cost equals its usage at $2/M input and $8/M output.
- Spend: the 22 row costs sum to **$0.625308**, equal to the recorded spend, within the $2.00 cap (planned worst case $1.706582).
- Reproduction: the unchanged scorers, run on `calls.jsonl`, return the report in `scored.json` **field for field, with no difference**.

## 2. The scoring discrepancies: what the frozen scorer does, and what it means

The frozen expected-output scorer (`score_expected`) matches an expected entry only when the expected `source` is contained in the
answer's `source_text`. Terminal punctuation is part of the expected source, so an answer whose `source_text` drops the final full
stop is not matched. The same containment is used for the routing check, and an exact set comparison is used for the duty check.

Every flag was traced to the rule that raised it (`frozen_flag_trace`, which reproduces each frozen count: `trace_counts_equal_frozen` is true for all 12 expected-output answers).

Arabic, v2-3 (frozen report: omissions 22, routing 6, invented 3, extra items 17):

| Class | Count | Example (quotations exact) |
|---|---|---|
| matching limitation (terminal punctuation) | 46 | JD: "…وفق جدول دوري." · expected source: "فحص أجهزة التكييف والمضخات وفق جدول دوري." · answer `source_text`: "فحص أجهزة التكييف والمضخات وفق جدول دوري" (same item, same category, no full stop) |
| genuine: AND pair not split | 2 | JD: "مهارات التواصل والعمل ضمن فريق." · call 1 has one item "مهارات التواصل والعمل ضمن فريق" for both "التواصل" and "العمل ضمن فريق" (call 3 likewise) |
| genuine: category error (soft skill in skills) | 4 | call 1 "التواصل", call 2 "التواصل" and "العمل ضمن فريق", call 3 "التواصل" are in `skills`; expected `soft_skills` (1 flagged by the frozen rule, 3 only by the diagnostic, see §2.2) |
| contestable expected label | 2 | "خبرة في أنظمة المراقبة عن بعد" in `experience` (calls 1 and 2); expected `skills` (see §2.3) |

So the frozen report's 22 omissions, 6 routing flags, 3 invented flags and 17 extra items are **46 matching limitations, not content defects**: the answers contained those items, and no source was invented. Every Arabic `source_text` in every answer is an exact substring of its JD (§3).

### 2.1 The matching limitation, stated precisely
- A missing final full stop makes the containment test fail. Across the three Arabic v2-3 answers this gives 20 omissions and 17 extra items (the same items, counted twice), 3 invented-duty flags (the exact set test on duty sources) and 6 routing flags. None of these is an invented source: each quoted `source_text` is verbatim in the JD.
- The diagnostic matcher differs only by ignoring trailing terminal punctuation on both sides. It never matches on fuzzy similarity. A genuinely missing entry is still reported as missing (test: `genuinely_missing`).
- In this data no shorter-quote case occurred (0); the classifier has a separate class for it and is tested on synthetic answers.

### 2.2 A second limitation, found in this audit
The frozen scorer checks category, importance and alternatives only for an entry it matched exactly. A category error on an entry that was missed only by the punctuation artifact is therefore not counted. The diagnostic adds these as **diagnostic-only flags**, which are not replacement scores: 3 genuine category errors and 2 contestable placements in Arabic v2-3 (listed in §2). The frozen report and `scored.json` are unchanged.

### 2.3 Expected labels that are contestable
"Experience with remote monitoring tools" (English) and "خبرة في أنظمة المراقبة عن بعد" (Arabic) are preferred items. Rule 5 places "required work experience … the kind of work" in experience and "tools, systems" in skills, so both placements are defensible. These are reported as contestable, not as defects. (The expected file was written before the calls and is not changed.)

### 2.4 What the frozen scorer cannot see
- Alternatives are compared as exact strings, so "Certified welder certificate" versus the JD's "Certified welder" counts as an OR failure. It is a genuine but minor alteration and is reported as such.
- Routing requires the expected source inside the answer's source. A correctly routed condition with a shorter verbatim quote would fail. This did not occur here.

## 3. Real model defects, checked answer by answer

### 3.1 TC20 (official checks, frozen labels; 5 calls per arm)

| Check | v2-3 (5 calls) | v2-5 (5 calls) | Evidence |
|---|---|---|---|
| C_RESP duty lines (16) | 4/5 (call 1 has 0 duty items) | **5/5** (16/16 every call) | `duty_lines_found` |
| C_COMP three competency lines | 5/5 | 5/5 | — |
| C_EXP_OR ICT list | 0/5 | 0/5 | in all 10 answers the item "1–3 years … ICT systems support, business applications, digital platforms, or software implementation projects" carries `alternatives: null` |
| C_FAM_OR familiarity list | 0/5 | 0/5 | in all 10 answers "Familiarity with business process documentation … or enterprise applications is an advantage" carries `alternatives: null` |
| C_LOCAL domain knowledge | 5/5 | 5/5 | — |
| C_LOCATION work location | 4/5 (call 5 puts the location sentence in `informational_items` as company description) | **5/5** | `location_statement` |
| C_REPORTING "submit deliverables" | 4/5 (call 1: the sentence is absent from the whole answer) | **1/5** (calls 2–5: absent from the whole answer; `informational_items` is empty) | `deliverables` absent from `raw` |
| C_AND communication, coordination, teamwork | 0/5 (one item in `skills`) | 0/5 (one item in `soft_skills`) | the single item "Strong communication, coordination, and teamwork skills" |

- **Reporting:** the failed reporting checks are **missing statements**, not wrong-list placements. For v2-5 in calls 2–5 the statement is not in the answer at all. For v2-3 call 1 the statement is not in the answer at all.
- **Soft skills:** v2-3 kept the three competency lines in `skills` in 5/5 calls; v2-5 moved them to `soft_skills` in 5/5 calls, but as one item, so the AND check still fails.
- **Other TC20 defects, not labelled:** "Good command of Arabic and English, including …" is one item in both arms (rule 4 asks for two). It does not change an official check.
- Weights: v2-3 totals were 85 (call 4) and 100 otherwise; v2-5 totals were 90 (call 3) and 100 otherwise. Rule 11 says the weights "do not need to total exactly 100", so these are not contract violations.

### 3.2 English (13 expected entries, 3 calls per arm)

| Defect | v2-3 | v2-5 |
|---|---|---|
| Communication and teamwork not split (AND) | 3/3 calls (Teamwork missing) | **0/3** |
| "Communication and teamwork skills" in `soft_skills` | 0/3 (in `skills`) | 2/3 (call 2 puts both in `skills`) |
| "Certified welder or boilermaker certificate" in `certifications` | 1/3 (calls 2, 3 in `skills`) | **3/3** |
| Certificate alternatives exact ("Certified welder", "boilermaker certificate") | 1/3 (calls 2, 3 altered to "Certified welder certificate") | **3/3** |
| "Arabic language skills are an advantage" in `skills` | 3/3 | 2/3 (call 1 in `other_requirements`: a genuine category error, since rule 8 makes a needed language a skill) |
| "Experience with remote monitoring tools" in `skills` | 3/3 | 0/3 (3/3 in `experience`: contestable, §2.3) |

### 3.3 Arabic (12 expected entries, 3 calls per arm)

| Defect | v2-3 | v2-5 |
|---|---|---|
| Soft skills ("التواصل", "العمل ضمن فريق") in `soft_skills` | 0/3 (in `skills`) | **3/3** |
| AND pair "مهارات التواصل والعمل ضمن فريق" split into two items | 1/3 split (calls 1 and 3 keep one item) | **3/3** |
| Company description (JD: "تدير الشركة ثلاثة مجمعات تجارية وتحتاج إلى فريق صيانة يعمل بكفاءة.") in `informational_items` | 3/3 (calls 1–3) | **0/3: absent from the whole answer** (verified in `raw`) |
| Degree alternatives ("الهندسة الميكانيكية", "الهندسة الكهربائية") | 3/3 | 3/3 |
| Certificate alternatives ("شهادة فني تبريد معتمدة", "شهادة لحام معتمدة") | 3/3 | 3/3 |
| "خبرة في أنظمة المراقبة عن بعد" in `skills` | 1/3 (call 3); in `experience` 2/3 (calls 1, 2) | 0/3 (3/3 in `experience`: contestable) |

## 4. Schema, education, importance, weights, invented content (all 22 answers)

- **Schema:** complete in all 22 answers (no missing key, no invalid enum).
- **Education alternatives:** kept in 5/5 TC20 answers for both arms (`education_alternatives_retained`); the degree alternatives in the two new JDs are correct in 6/6 answers per arm.
- **Importance:** no importance flag in any frozen or diagnostic class, for any answer.
- **Weights:** every answer's weights are whole numbers in 0–100 with at least one usable required category. Totals were 100 in 19 answers and 85, 90 or 110 in one each. Rule 11 allows any total, so none is a violation.
- **Invented content:** every `source_text` in all 22 answers is an exact substring of its JD. No duty is invented: every duty item in the two new JDs corresponds to a JD line, and every TC20 duty line is a JD line.

## 5. Comparison, corrected

**Genuine improvements (supported by this run):**
- English: AND split for the communication and teamwork pair (0 flags for v2-5 against 3 for v2-3); the certificate placed in `certifications` with exact alternatives in 3/3 (v2-3: correct in 1/3, two altered to "Certified welder certificate" and two in `skills`).
- Arabic: soft-skill items moved to `soft_skills` in 3/3 (v2-3 0/3), and the AND pair split in 3/3 (v2-3 1/3). The genuine Arabic category and AND defects fall from 6 (4 category, 2 AND) in v2-3 to 0 in v2-5; the company-description regression below is separate.
- TC20: the duty lines are complete in all 5 v2-5 calls (v2-3: 4 of 5), and the soft-skill items are placed in `soft_skills` in 5/5 (v2-3: 0/5).

**Genuine regressions (the candidate is worse on these):**
- TC20 reporting: the "submit deliverables" statement is missing from the whole answer in 4/5 v2-5 calls, and `informational_items` is empty in those calls (v2-3: missing in 1/5, empty in none).
- Arabic company description: absent in 3/3 v2-5 calls (present in 3/3 v2-3 calls).
- English: "Arabic language skills" placed in `other_requirements` in 1/3 v2-5 calls (rule 8 says a needed language is a skill).

**Scoring limitations (not defects of the model):**
- The terminal-punctuation containment test (46 Arabic flags, §2.1).
- The category, importance and alternatives checks are not applied to entries the containment test misses (§2.2).
- Exact string comparison for alternatives (§2.4).
- Two contestable expected labels (§2.3).

**Unresolved:**
- The ICT and familiarity OR lists (C_EXP_OR and C_FAM_OR) fail in all 20 TC20 answers of both arms. The candidate's OR wording did not change this.
- The comma-separated soft-skill list ("Strong communication, coordination, and teamwork skills") is one item in all 10 TC20 answers of both arms, so C_AND fails in both arms.
- The control's own variance is large (§6), so none of the TC20 differences above is established beyond the noise of this design.

## 6. Run-to-run variance of the control (the same prompt, model, JD and user message)

Two runs of the unchanged v2-3 prompt on gpt-4.1 with the TC20 JD disagree:

| Check | earlier run (stored `6eed1fd1`) | this run (v2-3 arm) |
|---|---|---|
| C_RESP | 2/5 | 4/5 |
| C_EXP_OR | 2/5 | 0/5 |
| C_LOCATION | 5/5 | 4/5 |
| C_REPORTING | 5/5 | 4/5 |

Four of the eight TC20 checks differ between two identical-input runs of the control. With five calls per arm, a difference of one or two passes on these checks is within run-to-run noise. The reporting regression is larger (4/5 missing against the control's 1/5 this run and 0/5 in the earlier run), but it rests on one run.

## 7. Caveats

- The two new JDs and their expected outputs were written alongside the candidate, by the same process. They are **not** independent held-out evidence. Their expected labels were fixed before the calls, but they reflect the same reading of the rules that the candidate was written from.
- Three JDs and 3 to 5 calls per arm: each per-JD count is small.
- The contestable labels (§2.3) are judgements about the rules, not facts.

## 8. Smallest next experiment (proposed, NOT run)

**A TC20-only replicate of both arms, 5 calls each (10 calls, same model, same settings, same user message, same caps and protocol).**

Why this, and no more: the two open questions are (a) whether the v2-5 reporting regression (4/5 missing) reproduces, and (b) whether the TC20 differences on duties and location exceed the control's own variance (§6). Ten calls answer both on the one JD where the labelled checks exist. Nothing else in the design needs to change first.

Budget (worst case, chars/2 input estimate, 6,000 output tokens per call): 5 × $0.077468 + 5 × $0.082712 = **$0.80 worst case**, against a proposed cap of $1.00. The stored TC20 calls cost about $0.02–0.04 each, so the expected spend is well below the cap.

Decision rule to set before the run: the reporting regression is confirmed if v2-5 has 3 or more calls missing the statement and the control has 1 or fewer. Duty and location gains are confirmed only if they hold in the replicate of both arms.
