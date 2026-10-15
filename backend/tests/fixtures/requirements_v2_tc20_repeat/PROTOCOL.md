# TC20-only repeat of the v2-3 versus v2-5 comparison: protocol and decision rules

Recorded BEFORE any call. The file's sha256 is pinned in `scripts/requirements_v2_tc20_repeat.py` and written into every run manifest.
Starting point: the audit at commit `1b94e5115abc162600edcc0d9df226b6939f737c` (`audit/AUDIT.md`).

## Design

- Arms: the unchanged full `criteria_extraction_v2-3` (sha256 `21a2f942…b784b43b`) and the unchanged `criteria_extraction_v2-5` (sha256 `f1569a8b…f1569a8b`, offline candidate).
- Five calls per arm, ten calls in total, alternating: v2-3 call 1, v2-5 call 1, v2-3 call 2, v2-5 call 2, … (order 1 to 10).
- Same stored TC20 JD, the same VPS job context (title "Technical Coordinator 20") and the same user message (sha256 `ff6b334a…240473`).
- Model `gpt-4.1-2025-04-14` (pinned snapshot; the returned model must equal it). Temperature 0.1, max_tokens 6000, JSON-object mode, timeout 90 seconds, no retries.
- Stop at the first failed call or the first unexpected returned model. Hard cost cap USD 1.00 (worst-case reservation USD 0.8009; the cap guard runs before each call).
- Labels, the TC20 scorer and the stored evidence are unchanged. The output directory must be new or empty.

## Decision rules (fixed before the calls)

1. **All eight TC20 checks, per call and per arm.** C_RESP, C_COMP, C_EXP_OR, C_FAM_OR, C_LOCAL, C_LOCATION, C_REPORTING and C_AND are reported for each of the ten calls and summed per arm, exactly as the frozen scorer gives them.
2. **Duty completeness and reporting coverage, against the previous paired run.** Duty completeness is the number of the sixteen duty lines found as `from_responsibilities` items. Reporting coverage is whether the "submit deliverables" statement appears anywhere in the answer and whether it is in `informational_items`. Both are compared per arm with the previous paired run (v2-3: duties 0 / 16 / 16 / 16 / 16, reporting missing in 1 of 5; v2-5: duties 16 in all five, reporting missing in 4 of 5).
3. **OR and AND reported separately.** C_EXP_OR and C_FAM_OR (OR alternatives) and C_AND (splitting of the communication, coordination and teamwork list) are reported as their own lines. A pass on any other check does not offset a failure on these three. A failure on them stays a stated open defect.
4. **Status of the result.** This repeat is exploratory evidence of consistency between two runs of the same design. It is not a test of statistical significance (five calls per arm) and not evidence of deployment readiness.
5. **v2-5 stays inactive.** Nothing in this run activates, registers or selects v2-5. Promotion would need the remaining defects resolved and broader regression testing (the 22-call comparison and more JDs), not a single repeat.

## Reading rules (what the repeat can and cannot say)

- A difference from the previous paired run is described as **reproduced** only if its direction is the same in this repeat for that arm. It is not described as significant.
- The reporting regression is described as **reproduced** only if v2-5 has three or more calls with the statement missing from the answer and v2-3 has one or fewer.
- Duty completeness is described as **reproduced** only if v2-5 again has sixteen of sixteen in all five calls and v2-3 again has at least one call with none.
- A change in any check not listed above is described as a single-run observation, not a finding.
