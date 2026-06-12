"""
Unit tests for services/criteria_matcher.py (Batch 2A-1).

Validates:
- object creation with required and optional fields
- default values
- type correctness
- serialisation-friendly structure (all fields JSON-serialisable)
- gap severity invariants
- MatchResult aggregate statistics defaults
"""
from __future__ import annotations

import dataclasses
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from services.criteria_matcher import (
    CriterionMatch,
    GapCandidate,
    MatchResult,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_json(obj) -> str:
    return json.dumps(dataclasses.asdict(obj))


def _make_criterion(
    criterion_text: str = "Microsoft Excel",
    dimension: str = "skills",
    required: bool = True,
    status: str = "MATCHED",
    confidence: float = 0.90,
    match_method: str = "exact",
    **kwargs,
) -> CriterionMatch:
    return CriterionMatch(
        criterion_text=criterion_text,
        dimension=dimension,
        required=required,
        status=status,
        confidence=confidence,
        match_method=match_method,
        **kwargs,
    )


# ── CriterionMatch ────────────────────────────────────────────────────────────

class TestCriterionMatch:
    def test_matched_criterion(self):
        cm = _make_criterion(
            criterion_text="Microsoft Excel",
            status="MATCHED",
            confidence=0.95,
            match_method="exact",
            supporting_evidence=["Excel proficiency listed in skills section"],
            evidence_confidence=[0.95],
        )
        assert cm.criterion_text == "Microsoft Excel"
        assert cm.status == "MATCHED"
        assert cm.confidence == 0.95
        assert len(cm.supporting_evidence) == 1
        assert cm.matched_via_translation is False
        assert cm.original_cv_term == ""
        assert cm.partial_reason == ""

    def test_partial_criterion(self):
        cm = _make_criterion(
            criterion_text="5 years experience",
            dimension="experience",
            status="PARTIAL",
            confidence=0.45,
            match_method="inferred",
            partial_reason="2 of 5 required years found",
        )
        assert cm.status == "PARTIAL"
        assert cm.partial_reason == "2 of 5 required years found"

    def test_absent_criterion(self):
        cm = _make_criterion(
            criterion_text="PMP Certification",
            dimension="certifications",
            status="ABSENT",
            confidence=0.05,
            match_method="absent",
        )
        assert cm.status == "ABSENT"
        assert cm.confidence == 0.05
        assert cm.match_method == "absent"

    def test_cross_lingual_match(self):
        cm = _make_criterion(
            criterion_text="Microsoft Excel",
            status="MATCHED",
            confidence=0.82,
            match_method="fuzzy",
            matched_via_translation=True,
            original_cv_term="إكسل",
        )
        assert cm.matched_via_translation is True
        assert cm.original_cv_term == "إكسل"

    def test_preferred_criterion(self):
        cm = _make_criterion(
            criterion_text="Tableau",
            required=False,
            status="ABSENT",
            confidence=0.0,
            match_method="absent",
        )
        assert cm.required is False

    def test_defaults(self):
        cm = _make_criterion()
        assert cm.supporting_evidence == []
        assert cm.evidence_confidence == []
        assert cm.partial_reason == ""
        assert cm.matched_via_translation is False
        assert cm.original_cv_term == ""

    def test_all_match_methods(self):
        for method in ("exact", "normalised", "fuzzy", "semantic", "inferred", "absent"):
            cm = _make_criterion(match_method=method, status="MATCHED", confidence=0.5)
            assert cm.match_method == method

    def test_all_dimensions(self):
        for dim in ("skills", "experience", "education", "certifications",
                    "soft_skills", "domain_knowledge", "other"):
            cm = _make_criterion(dimension=dim)
            assert cm.dimension == dim

    def test_json_serialisable(self):
        cm = _make_criterion(
            supporting_evidence=["Excel listed in CV skills"],
            evidence_confidence=[0.95],
        )
        payload = _to_json(cm)
        data = json.loads(payload)
        assert data["criterion_text"] == "Microsoft Excel"
        assert data["status"] == "MATCHED"
        assert isinstance(data["confidence"], float)
        assert isinstance(data["supporting_evidence"], list)
        assert isinstance(data["evidence_confidence"], list)

    def test_multiple_evidence_items(self):
        cm = _make_criterion(
            status="MATCHED",
            confidence=0.85,
            supporting_evidence=[
                "Excel listed in skills section",
                "Prepared Excel reports for management",
            ],
            evidence_confidence=[0.95, 0.45],
        )
        assert len(cm.supporting_evidence) == 2
        assert len(cm.evidence_confidence) == 2

    def test_dataclass_fields(self):
        field_names = {f.name for f in dataclasses.fields(CriterionMatch)}
        required = {
            "criterion_text", "dimension", "required", "status",
            "confidence", "match_method", "supporting_evidence",
            "evidence_confidence", "partial_reason",
            "matched_via_translation", "original_cv_term",
        }
        assert required.issubset(field_names)


# ── GapCandidate ──────────────────────────────────────────────────────────────

class TestGapCandidate:
    def _make_absent_criterion(self, required: bool = True) -> CriterionMatch:
        return _make_criterion(
            criterion_text="PMP Certification",
            dimension="certifications",
            required=required,
            status="ABSENT",
            confidence=0.05,
            match_method="absent",
        )

    def test_blocking_gap(self):
        gap = GapCandidate(
            criterion=self._make_absent_criterion(required=True),
            severity="BLOCKING",
        )
        assert gap.severity == "BLOCKING"
        assert gap.suppressed is False
        assert gap.compensating_evidence == []
        assert gap.compensating_confidence == 0.0
        assert gap.suppression_reason == ""

    def test_significant_gap_with_compensation(self):
        gap = GapCandidate(
            criterion=self._make_absent_criterion(required=True),
            severity="SIGNIFICANT",
            compensating_evidence=["10 years relevant project management experience"],
            compensating_confidence=0.65,
        )
        assert gap.severity == "SIGNIFICANT"
        assert gap.compensating_confidence == 0.65
        assert not gap.suppressed  # 0.65 < 0.70 threshold

    def test_minor_gap_suppressed(self):
        gap = GapCandidate(
            criterion=self._make_absent_criterion(required=False),
            severity="MINOR",
            compensating_evidence=["Strong project management background"],
            compensating_confidence=0.75,
            suppressed=True,
            suppression_reason="Strong PM experience compensates for missing cert",
        )
        assert gap.suppressed is True
        assert gap.suppression_reason != ""

    def test_all_severities(self):
        for severity in ("BLOCKING", "SIGNIFICANT", "MINOR"):
            gap = GapCandidate(
                criterion=self._make_absent_criterion(),
                severity=severity,
            )
            assert gap.severity == severity

    def test_defaults(self):
        gap = GapCandidate(
            criterion=self._make_absent_criterion(),
            severity="BLOCKING",
        )
        assert gap.compensating_evidence == []
        assert gap.compensating_confidence == 0.0
        assert gap.suppressed is False
        assert gap.suppression_reason == ""

    def test_json_serialisable(self):
        gap = GapCandidate(
            criterion=self._make_absent_criterion(),
            severity="BLOCKING",
        )
        payload = _to_json(gap)
        data = json.loads(payload)
        assert data["severity"] == "BLOCKING"
        assert data["suppressed"] is False
        assert isinstance(data["criterion"], dict)
        assert data["criterion"]["status"] == "ABSENT"

    def test_criterion_embedded_correctly(self):
        criterion = self._make_absent_criterion()
        gap = GapCandidate(criterion=criterion, severity="SIGNIFICANT")
        # Access the nested criterion through the dataclass
        assert gap.criterion.criterion_text == "PMP Certification"
        assert gap.criterion.dimension == "certifications"
        assert gap.criterion.status == "ABSENT"

    def test_dataclass_fields(self):
        field_names = {f.name for f in dataclasses.fields(GapCandidate)}
        required = {
            "criterion", "severity", "compensating_evidence",
            "compensating_confidence", "suppressed", "suppression_reason",
        }
        assert required.issubset(field_names)


# ── MatchResult ───────────────────────────────────────────────────────────────

class TestMatchResult:
    def _minimal_result(self, **kwargs) -> MatchResult:
        defaults = dict(
            application_id="app-uuid-001",
            job_id="job-uuid-001",
            criteria_version="abc123def456",
        )
        defaults.update(kwargs)
        return MatchResult(**defaults)

    def test_minimal_creation(self):
        mr = self._minimal_result()
        assert mr.application_id == "app-uuid-001"
        assert mr.job_id == "job-uuid-001"
        assert mr.criteria_version == "abc123def456"

    def test_defaults(self):
        mr = self._minimal_result()
        assert mr.matches == []
        assert mr.gap_candidates == []
        assert mr.required_match_pct == 0.0
        assert mr.preferred_match_pct == 0.0
        assert mr.partial_match_pct == 0.0
        assert mr.blocking_gap_count == 0
        assert mr.algorithmic_scores == {}
        assert mr.matcher_version == "0.0.0"
        assert mr.matching_method_summary == {}

    def test_with_matches(self):
        matches = [
            _make_criterion("Excel", status="MATCHED", confidence=0.95),
            _make_criterion("Python", status="ABSENT", confidence=0.0, match_method="absent"),
        ]
        mr = self._minimal_result(
            matches=matches,
            required_match_pct=50.0,
            blocking_gap_count=1,
        )
        assert len(mr.matches) == 2
        assert mr.required_match_pct == 50.0
        assert mr.blocking_gap_count == 1

    def test_algorithmic_scores(self):
        scores = {
            "skills": 72.5,
            "experience": 85.0,
            "education": 60.0,
            "certifications": 0.0,
            "soft_skills": 65.0,
            "domain_knowledge": 70.0,
            "other": 50.0,
        }
        mr = self._minimal_result(algorithmic_scores=scores)
        assert mr.algorithmic_scores["skills"] == 72.5
        assert mr.algorithmic_scores["experience"] == 85.0

    def test_matching_method_summary(self):
        mr = self._minimal_result(
            matching_method_summary={"exact": 3, "fuzzy": 2, "absent": 1}
        )
        assert mr.matching_method_summary["exact"] == 3

    def test_json_serialisable_minimal(self):
        mr = self._minimal_result()
        payload = _to_json(mr)
        data = json.loads(payload)
        assert data["application_id"] == "app-uuid-001"
        assert data["matches"] == []
        assert data["algorithmic_scores"] == {}

    def test_json_serialisable_with_matches(self):
        criterion = _make_criterion(
            "Excel",
            supporting_evidence=["Excel proficiency listed"],
            evidence_confidence=[0.95],
        )
        gap_crit = _make_criterion(
            "PMP", dimension="certifications", status="ABSENT",
            confidence=0.0, match_method="absent",
        )
        gap = GapCandidate(criterion=gap_crit, severity="BLOCKING")
        mr = MatchResult(
            application_id="app-001",
            job_id="job-001",
            criteria_version="deadbeef",
            matches=[criterion],
            gap_candidates=[gap],
            required_match_pct=50.0,
            blocking_gap_count=1,
            algorithmic_scores={"skills": 75.0, "certifications": 0.0},
            matcher_version="1.0.0",
            matching_method_summary={"exact": 1, "absent": 1},
        )
        payload = _to_json(mr)
        data = json.loads(payload)
        assert len(data["matches"]) == 1
        assert len(data["gap_candidates"]) == 1
        assert data["gap_candidates"][0]["severity"] == "BLOCKING"
        assert data["algorithmic_scores"]["skills"] == 75.0

    def test_aggregate_statistics_types(self):
        mr = self._minimal_result(
            required_match_pct=66.7,
            preferred_match_pct=33.3,
            partial_match_pct=16.7,
            blocking_gap_count=2,
        )
        assert isinstance(mr.required_match_pct, float)
        assert isinstance(mr.preferred_match_pct, float)
        assert isinstance(mr.partial_match_pct, float)
        assert isinstance(mr.blocking_gap_count, int)

    def test_dataclass_fields(self):
        field_names = {f.name for f in dataclasses.fields(MatchResult)}
        required = {
            "application_id", "job_id", "criteria_version",
            "matches", "gap_candidates",
            "required_match_pct", "preferred_match_pct",
            "partial_match_pct", "blocking_gap_count",
            "algorithmic_scores", "matcher_version",
            "matching_method_summary",
        }
        assert required.issubset(field_names)

    def test_criteria_version_is_string(self):
        mr = self._minimal_result(criteria_version="abc123456789abcd")
        assert isinstance(mr.criteria_version, str)

    def test_multiple_gap_candidates(self):
        gaps = [
            GapCandidate(
                criterion=_make_criterion(f"Skill {i}", status="ABSENT",
                                          confidence=0.0, match_method="absent"),
                severity="BLOCKING" if i == 0 else "SIGNIFICANT",
            )
            for i in range(3)
        ]
        mr = self._minimal_result(gap_candidates=gaps, blocking_gap_count=1)
        assert len(mr.gap_candidates) == 3
        assert mr.gap_candidates[0].severity == "BLOCKING"
        assert mr.gap_candidates[1].severity == "SIGNIFICANT"
