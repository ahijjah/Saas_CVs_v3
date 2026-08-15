# Investigation: Education & Experience Scoring Relevance Issues

## Overview
Investigation into claims that education and experience scoring ignore field/domain relevance and only check level/duration independently.

## Findings

### PROBLEM 1: EDUCATION FIELD-OF-STUDY MATCHING ✓ CONFIRMED

**Root Cause:**
The `_match_education()` function in `/backend/services/criteria_matcher.py` (lines 729-777) only evaluates `minimum_level` (degree level) and completely ignores `fields_of_study`.

**Code Analysis:**
```python
# Line 736-738: Only reads minimum_level
min_level = edu_criteria.get("minimum_level", "None")
if not min_level or min_level == "None":
    return []
# Line 752: Only checks degree level comparison
if actual_idx >= required_idx:
    status, confidence, partial_reason = "MATCHED", 0.90, ""
```

**What Job Analysis Extracts:**
The job criteria mapper (`llm_criteria_mapper.py`, lines 411-421) correctly extracts BOTH:
- `minimum_level`: "Bachelor's" (required=True)
- `fields_of_study`: ["Computer Science"] (required=False, created as separate criterion)

**The Mismatch:**
- Job analysis: Creates "Field of study: Computer Science" as a separate non-required criterion
- Scoring logic: Never evaluates field_of_study in education matching
- Result: A candidate with "Bachelor's in Business Administration" receives FULL CREDIT for "Bachelor's in Computer Science" requirement just because the degree level matches

**Proof of Bug:**
Created test case: Candidate has "Bachelor's in Business Administration" for job requiring "Bachelor's in Computer Science"
- Expected: PARTIAL or ABSENT status
- Actual: MATCHED with 0.90 confidence
- Evidence: Only degree level was checked, field ignored

**Test Results:**
```
Candidate's Education:
  Degree: Bachelor's
  Field: Business Administration  ← IGNORED!
  Institution: State University

Job Requirements:
  Minimum Degree: Bachelor's
  Required Fields: ['Computer Science']

MATCHING RESULTS:
Criterion: 'Minimum education: Bachelor's'
  Status: MATCHED  ← WRONG! Should be PARTIAL at best
  Confidence: 0.90
  Match Method: exact
```

---

### PROBLEM 2: EXPERIENCE DOMAIN-RELEVANCE MATCHING ⚠️ PARTIALLY WORKING

**Status:** NOT a critical bug, but has known limitations

**Current Behavior:**
The `_match_experience()` function (lines 527-726) DOES have relevance checking, but it works differently than education:

1. **When `numeric_passed` AND `has_relevance_data`:** Runs domain relevance check
   - Uses keyword matching + fuzzy scoring
   - Returns PARTIAL if relevance fails (good!)
   - Returns MATCHED if relevance passes (potential issue)

2. **When numeric fails:** Falls back to pure numeric (acceptable)

**Proof with Test Case:**
Candidate: "5 years as Accounting Manager"
Job Requirement: "3+ years in HR with recruitment/hiring focus"

Result: PARTIAL with confidence 0.45
- Reason: "5 years total experience, but relevance to role not verified"
- This is CORRECT behavior - the code detected the domain mismatch

**Known Limitation (Already Documented in Code):**
Lines 595-598 in `criteria_matcher.py`:
```python
# "This is terminology-dependent heuristic, NOT semantic matching.
#  It correctly filters same-role/different-domain false positives
#  (e.g., "Laboratory Coordinator" vs "HR Coordinator") when both texts
#  explicitly share a known keyword. However, it can silently pass through
#  unrelated candidates via the 85% fuzzy-score fallback for domains/synonyms
#  outside this keyword list"
```

**Domain Keywords List:**
File: `/backend/services/criteria_matcher.py` line 600-605
```python
domain_keywords = {
    "hr", "recruitment", "hiring", "staffing", "payroll",
    "training", "development", "management", "marketing", "finance",
    "accounting", "sales", "engineering", "it", "ict", "operations",
    "customer service", "support", "logistics", "supply chain",
}
```

**Limitation:** This is a hardcoded list. Domains outside this list rely on 85%+ fuzzy matching, which can produce false positives (e.g., "Accounting Manager" might fuzzy-match "HR Manager" at 85%+ due to shared "Manager" token).

---

## File Locations Summary

### Critical Files
1. **`/backend/services/criteria_matcher.py`** (1167 lines)
   - `_match_education()` lines 729-777 (INCOMPLETE - ignores field_of_study)
   - `_match_experience()` lines 527-726 (WORKING but with known fuzzy-match limitations)

2. **`/backend/services/cv_evidence.py`**
   - `EducationEvidence` class (line 113): HAS `field_of_study` field
   - `ExperienceEvidence` class (line ~90): HAS `responsibilities` field for domain context

3. **`/backend/services/llm_criteria_mapper.py`**
   - Lines 403-421: Job analysis correctly extracts both level AND field_of_study

4. **`/backend/tests/test_criteria_matcher.py`**
   - Tests for education (lines 890-924): Only test degree level, NOT field_of_study
   - No tests exist for field_of_study matching

---

## Real Data Confirmation

Checked `/backend/tests/test_criteria_matcher.py` helper function `_make_criteria()` (line 501):
- Always creates `"fields_of_study": []` (line 524)
- No test ever validates field_of_study matching
- This indicates the feature was never implemented in scoring logic

---

## Summary Table

| Issue | Type | Severity | Status | Evidence |
|-------|------|----------|--------|----------|
| Education field-of-study ignored | Logic bug | HIGH | CONFIRMED | Test: Bachelor's in Business Admin passes "Bachelor's in CS" requirement |
| Experience domain fuzzy-match false positives | Limitation | MEDIUM | KNOWN ISSUE | Code comments acknowledge 85% fuzzy fallback risk; code exists to mitigate |

---

## Next Steps (Not Executed - Investigation Only)

1. **Fix Education Matching:**
   - Enhance `_match_education()` to evaluate field_of_study
   - Compare candidate's education fields against job's required fields_of_study
   - Return PARTIAL or ABSENT if fields don't match

2. **Improve Experience Matching:**
   - Replace hardcoded domain_keywords with semantic similarity or LLM-based domain classification
   - OR extend domain_keywords list with more comprehensive coverage
   - Ensure job domains and candidate domains are compared semantically, not just via fuzzy token matching

3. **Add Tests:**
   - Education: Test field_of_study matching scenarios (matched/partial/absent)
   - Experience: Test semantic domain mismatch detection

---

## Conclusion

**PROBLEM 1 (Education):** ✓ Real and reproducible bug. Field-of-study requirements are completely ignored in scoring.

**PROBLEM 2 (Experience):** ⚠️ Partially working. The code does check domain relevance and correctly returns PARTIAL for mismatches, but has known limitations with fuzzy matching for domains outside a hardcoded keyword list.

Investigation complete. Ready to propose fixes on user authorization.
