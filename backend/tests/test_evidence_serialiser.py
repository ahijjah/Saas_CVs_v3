"""
Unit tests for services/evidence_serialiser.py (Batch 2A-2).

Validates:
- CVFacts round-trip (to_dict → from_dict produces identical object)
- MatchResult round-trip
- nested evidence lists survive correctly
- Arabic / mixed-language text preserved byte-for-byte
- empty / default objects serialise and deserialise correctly
- invalid / missing structure produces safe errors or safe defaults
- _schema key present after serialisation
- json.dumps compatibility (all serialised values are JSON-safe primitives)
"""
from __future__ import annotations

import dataclasses
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from services.cv_evidence import (
    CVFacts,
    CertificationEvidence,
    DomainSignal,
    EducationEvidence,
    ExperienceEvidence,
    SkillEvidence,
    SoftSkillSignal,
)
from services.criteria_matcher import (
    CriterionMatch,
    GapCandidate,
    MatchResult,
)
from services.evidence_serialiser import (
    SerialisationError,
    _CVFACTS_SCHEMA,
    _MATCHRESULT_SCHEMA,
    cvfacts_from_dict,
    cvfacts_to_dict,
    matchresult_from_dict,
    matchresult_to_dict,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_skill(
    skill_name: str = "Microsoft Excel",
    raw_text: str = "Excel",
    explicit: bool = True,
    confidence: float = 0.95,
    language: str = "en",
    section_hint: str = "skills",
    inference_basis: str = "",
) -> SkillEvidence:
    return SkillEvidence(
        skill_name=skill_name,
        raw_text=raw_text,
        explicit=explicit,
        confidence=confidence,
        context_snippet="Proficient in Excel",
        language=language,
        section_hint=section_hint,
        inference_basis=inference_basis,
    )


def _make_experience(employer: str = "Acme Corp", years: float = 3.0) -> ExperienceEvidence:
    return ExperienceEvidence(
        employer=employer,
        role_title="Records Officer",
        years=years,
        responsibilities=["Managed filing", "Prepared reports"],
        domain_signals=["records management"],
        skill_signals=["Excel"],
        raw_text="Records Officer at Acme Corp",
    )


def _make_education(degree_level: str = "Bachelor's", year: int | None = 2018) -> EducationEvidence:
    return EducationEvidence(
        degree_level=degree_level,
        field_of_study="Business Administration",
        institution="State University",
        year=year,
    )


def _make_cert(name: str = "PMP", year: int | None = 2021) -> CertificationEvidence:
    return CertificationEvidence(
        name=name,
        raw_text="PMP – Project Management Professional",
        issuer="PMI",
        year=year,
    )


def _make_soft_skill() -> SoftSkillSignal:
    return SoftSkillSignal(
        soft_skill_category="leadership",
        evidence_phrase="led a team of 8",
        confidence=0.80,
        inference_basis="led a team → leadership",
    )


def _make_domain_signal() -> DomainSignal:
    return DomainSignal(
        domain_term="records management",
        frequency=3,
        context_snippets=["supervised records management"],
    )


def _make_full_cvfacts() -> CVFacts:
    return CVFacts(
        language="en",
        total_char_count=2500,
        skills=[_make_skill()],
        experience=[_make_experience()],
        education=[_make_education()],
        certifications=[_make_cert()],
        soft_skill_signals=[_make_soft_skill()],
        domain_signals=[_make_domain_signal()],
        total_experience_years=3.0,
        highest_education_level="Bachelor's",
        skill_names_normalised=["Microsoft Excel"],
        extractor_version="1.0.0",
        extraction_method="rule_based_v1",
        extraction_warnings=["date unparseable in block 2"],
    )


def _make_criterion_match(
    criterion_text: str = "Microsoft Excel",
    status: str = "MATCHED",
    required: bool = True,
    confidence: float = 0.95,
) -> CriterionMatch:
    return CriterionMatch(
        criterion_text=criterion_text,
        dimension="skills",
        required=required,
        status=status,
        confidence=confidence,
        match_method="exact",
        supporting_evidence=["Excel listed in CV"],
        evidence_confidence=[0.95],
        matched_via_translation=False,
        original_cv_term="",
    )


def _make_gap(severity: str = "BLOCKING") -> GapCandidate:
    return GapCandidate(
        criterion=_make_criterion_match(status="ABSENT", confidence=0.0),
        severity=severity,
        compensating_evidence=[],
        compensating_confidence=0.0,
        suppressed=False,
        suppression_reason="",
    )


def _make_full_matchresult() -> MatchResult:
    return MatchResult(
        application_id="app-uuid-001",
        job_id="job-uuid-001",
        criteria_version="abc123def456",
        matches=[
            _make_criterion_match("Microsoft Excel", "MATCHED"),
            _make_criterion_match("Python", "ABSENT", confidence=0.0),
        ],
        gap_candidates=[_make_gap("BLOCKING")],
        required_match_pct=50.0,
        preferred_match_pct=100.0,
        partial_match_pct=0.0,
        blocking_gap_count=1,
        algorithmic_scores={"skills": 50.0, "experience": 80.0},
        matcher_version="1.0.0",
        matching_method_summary={"exact": 1, "absent": 1},
    )


# ── CVFacts — serialisation ───────────────────────────────────────────────────

class TestCVFactsToDict:
    def test_produces_dict(self):
        facts = _make_full_cvfacts()
        d = cvfacts_to_dict(facts)
        assert isinstance(d, dict)

    def test_schema_key_present(self):
        d = cvfacts_to_dict(_make_full_cvfacts())
        assert "_schema" in d
        assert d["_schema"] == _CVFACTS_SCHEMA

    def test_top_level_fields_present(self):
        d = cvfacts_to_dict(_make_full_cvfacts())
        for key in ("language", "total_char_count", "skills", "experience",
                    "education", "certifications", "soft_skill_signals",
                    "domain_signals", "total_experience_years",
                    "highest_education_level", "skill_names_normalised",
                    "extractor_version", "extraction_method",
                    "extraction_warnings"):
            assert key in d, f"Missing key: {key}"

    def test_skills_serialised_as_list_of_dicts(self):
        d = cvfacts_to_dict(_make_full_cvfacts())
        assert isinstance(d["skills"], list)
        assert isinstance(d["skills"][0], dict)
        assert d["skills"][0]["skill_name"] == "Microsoft Excel"

    def test_nested_experience_serialised(self):
        d = cvfacts_to_dict(_make_full_cvfacts())
        exp = d["experience"][0]
        assert exp["employer"] == "Acme Corp"
        assert exp["years"] == 3.0
        assert isinstance(exp["responsibilities"], list)

    def test_json_safe_output(self):
        d = cvfacts_to_dict(_make_full_cvfacts())
        # Must not raise
        payload = json.dumps(d, ensure_ascii=False)
        assert isinstance(payload, str)

    def test_empty_cvfacts_serialises(self):
        facts = CVFacts(language="en", total_char_count=0)
        d = cvfacts_to_dict(facts)
        assert d["skills"] == []
        assert d["experience"] == []
        assert d["total_experience_years"] == 0.0

    def test_extractor_version_preserved(self):
        facts = CVFacts(language="en", total_char_count=100, extractor_version="2.3.1")
        d = cvfacts_to_dict(facts)
        assert d["extractor_version"] == "2.3.1"


# ── CVFacts — deserialisation ─────────────────────────────────────────────────

class TestCVFactsFromDict:
    def test_round_trip_full(self):
        original = _make_full_cvfacts()
        restored = cvfacts_from_dict(cvfacts_to_dict(original))
        assert dataclasses.asdict(restored) == dataclasses.asdict(original)

    def test_round_trip_empty(self):
        original = CVFacts(language="ar", total_char_count=0)
        restored = cvfacts_from_dict(cvfacts_to_dict(original))
        assert restored.language == "ar"
        assert restored.skills == []
        assert restored.total_char_count == 0

    def test_round_trip_preserves_all_skill_fields(self):
        skill = _make_skill(
            skill_name="Python",
            raw_text="Python 3.11",
            explicit=False,
            confidence=0.45,
            inference_basis="wrote Python scripts → Python",
        )
        facts = CVFacts(language="en", total_char_count=500, skills=[skill])
        restored = cvfacts_from_dict(cvfacts_to_dict(facts))
        r_skill = restored.skills[0]
        assert r_skill.skill_name == "Python"
        assert r_skill.raw_text == "Python 3.11"
        assert r_skill.explicit is False
        assert r_skill.confidence == 0.45
        assert r_skill.inference_basis == "wrote Python scripts → Python"

    def test_round_trip_preserves_education_year_none(self):
        edu = _make_education(year=None)
        facts = CVFacts(language="en", total_char_count=200, education=[edu])
        restored = cvfacts_from_dict(cvfacts_to_dict(facts))
        assert restored.education[0].year is None

    def test_round_trip_preserves_cert_year_int(self):
        cert = _make_cert(year=2022)
        facts = CVFacts(language="en", total_char_count=100, certifications=[cert])
        restored = cvfacts_from_dict(cvfacts_to_dict(facts))
        assert restored.certifications[0].year == 2022

    def test_missing_keys_use_defaults(self):
        # Minimal valid dict — required fields only
        facts = cvfacts_from_dict({"language": "en", "total_char_count": 500})
        assert facts.skills == []
        assert facts.experience == []
        assert facts.total_experience_years == 0.0
        assert facts.extractor_version == "0.0.0"
        assert facts.extraction_method == "rule_based_v1"

    def test_completely_empty_dict_uses_defaults(self):
        facts = cvfacts_from_dict({})
        assert facts.language == "en"
        assert facts.total_char_count == 0
        assert facts.skills == []

    def test_extra_unknown_keys_ignored(self):
        d = {"language": "en", "total_char_count": 100, "_schema": "2a.1",
             "unknown_future_field": "some_value"}
        facts = cvfacts_from_dict(d)
        assert facts.language == "en"

    def test_malformed_skills_list_item_skipped(self):
        # The second item is not a dict — should be skipped
        d = {
            "language": "en",
            "total_char_count": 100,
            "skills": [
                {"skill_name": "Excel", "raw_text": "Excel", "explicit": True,
                 "confidence": 0.9, "context_snippet": "x", "language": "en",
                 "section_hint": "skills"},
                "not a dict",
                None,
            ],
        }
        facts = cvfacts_from_dict(d)
        assert len(facts.skills) == 1
        assert facts.skills[0].skill_name == "Excel"

    def test_wrong_type_scalar_uses_default(self):
        d = {"language": "en", "total_char_count": "not_an_int"}
        facts = cvfacts_from_dict(d)
        assert facts.total_char_count == 0

    def test_non_dict_raises_serialisation_error(self):
        for bad in (None, "string", 42, [], True):
            with pytest.raises(SerialisationError):
                cvfacts_from_dict(bad)

    def test_non_list_skills_falls_back_to_empty(self):
        d = {"language": "en", "total_char_count": 100, "skills": "not a list"}
        facts = cvfacts_from_dict(d)
        assert facts.skills == []

    def test_extraction_warnings_list_preserved(self):
        original = _make_full_cvfacts()
        restored = cvfacts_from_dict(cvfacts_to_dict(original))
        assert restored.extraction_warnings == ["date unparseable in block 2"]

    def test_multiple_skills_all_restored(self):
        facts = CVFacts(
            language="en",
            total_char_count=1000,
            skills=[
                _make_skill("Excel"),
                _make_skill("Python", raw_text="Python", confidence=0.85),
                _make_skill("Word", raw_text="Word", confidence=0.90),
            ],
        )
        restored = cvfacts_from_dict(cvfacts_to_dict(facts))
        assert len(restored.skills) == 3
        names = [s.skill_name for s in restored.skills]
        assert "Excel" in names and "Python" in names and "Word" in names


# ── Arabic / multilingual text preservation ───────────────────────────────────

class TestArabicTextPreservation:
    ARABIC_SKILL = "إكسل"
    ARABIC_EMPLOYER = "شركة التقنية المتقدمة"
    ARABIC_SNIPPET = "مهارات: إكسل، وورد، باوربوينت"

    def test_arabic_skill_raw_text_preserved(self):
        skill = SkillEvidence(
            skill_name="Microsoft Excel",
            raw_text=self.ARABIC_SKILL,
            explicit=True,
            confidence=0.82,
            context_snippet=self.ARABIC_SNIPPET,
            language="ar",
            section_hint="skills",
        )
        facts = CVFacts(language="ar", total_char_count=500, skills=[skill])
        restored = cvfacts_from_dict(cvfacts_to_dict(facts))
        assert restored.skills[0].raw_text == self.ARABIC_SKILL
        assert restored.skills[0].context_snippet == self.ARABIC_SNIPPET
        assert restored.skills[0].language == "ar"

    def test_arabic_employer_preserved(self):
        exp = ExperienceEvidence(
            employer=self.ARABIC_EMPLOYER,
            role_title="مسؤول الأرشفة",
            years=4.0,
            responsibilities=["إدارة ملفات الموظفين", "الأرشفة الإلكترونية"],
        )
        facts = CVFacts(language="ar", total_char_count=800, experience=[exp])
        restored = cvfacts_from_dict(cvfacts_to_dict(facts))
        r_exp = restored.experience[0]
        assert r_exp.employer == self.ARABIC_EMPLOYER
        assert r_exp.role_title == "مسؤول الأرشفة"
        assert r_exp.responsibilities[0] == "إدارة ملفات الموظفين"

    def test_arabic_json_dumps_round_trip(self):
        skill = SkillEvidence(
            skill_name="Microsoft Excel",
            raw_text=self.ARABIC_SKILL,
            explicit=True,
            confidence=0.80,
            context_snippet=self.ARABIC_SNIPPET,
            language="ar",
            section_hint="skills",
        )
        facts = CVFacts(language="ar", total_char_count=300, skills=[skill])
        serialised = json.dumps(cvfacts_to_dict(facts), ensure_ascii=False)
        assert self.ARABIC_SKILL in serialised
        assert self.ARABIC_SNIPPET in serialised

    def test_mixed_language_facts(self):
        skill_en = _make_skill("Excel", raw_text="Excel", language="en")
        skill_ar = _make_skill("Microsoft Excel", raw_text="إكسل", language="ar")
        facts = CVFacts(
            language="mixed",
            total_char_count=600,
            skills=[skill_en, skill_ar],
        )
        restored = cvfacts_from_dict(cvfacts_to_dict(facts))
        assert restored.language == "mixed"
        assert restored.skills[0].language == "en"
        assert restored.skills[1].language == "ar"
        assert restored.skills[1].raw_text == "إكسل"

    def test_arabic_inference_basis_preserved(self):
        skill = _make_skill(
            inference_basis="أعدّ التقارير في إكسل → مهارة إكسل"
        )
        facts = CVFacts(language="ar", total_char_count=200, skills=[skill])
        restored = cvfacts_from_dict(cvfacts_to_dict(facts))
        assert restored.skills[0].inference_basis == "أعدّ التقارير في إكسل → مهارة إكسل"

    def test_arabic_domain_signal_preserved(self):
        sig = DomainSignal(
            domain_term="إدارة السجلات",
            frequency=2,
            context_snippets=["تشمل مهام إدارة السجلات والتوثيق"],
        )
        facts = CVFacts(language="ar", total_char_count=400, domain_signals=[sig])
        restored = cvfacts_from_dict(cvfacts_to_dict(facts))
        assert restored.domain_signals[0].domain_term == "إدارة السجلات"
        assert restored.domain_signals[0].context_snippets[0] == "تشمل مهام إدارة السجلات والتوثيق"


# ── MatchResult — serialisation ───────────────────────────────────────────────

class TestMatchResultToDict:
    def test_produces_dict(self):
        d = matchresult_to_dict(_make_full_matchresult())
        assert isinstance(d, dict)

    def test_schema_key_present(self):
        d = matchresult_to_dict(_make_full_matchresult())
        assert "_schema" in d
        assert d["_schema"] == _MATCHRESULT_SCHEMA

    def test_top_level_fields_present(self):
        d = matchresult_to_dict(_make_full_matchresult())
        for key in ("application_id", "job_id", "criteria_version",
                    "matches", "gap_candidates", "required_match_pct",
                    "preferred_match_pct", "partial_match_pct",
                    "blocking_gap_count", "algorithmic_scores",
                    "matcher_version", "matching_method_summary"):
            assert key in d, f"Missing key: {key}"

    def test_matches_serialised_as_list_of_dicts(self):
        d = matchresult_to_dict(_make_full_matchresult())
        assert isinstance(d["matches"], list)
        assert isinstance(d["matches"][0], dict)
        assert d["matches"][0]["criterion_text"] == "Microsoft Excel"

    def test_gap_candidates_contain_nested_criterion(self):
        d = matchresult_to_dict(_make_full_matchresult())
        assert len(d["gap_candidates"]) == 1
        gap = d["gap_candidates"][0]
        assert "criterion" in gap
        assert isinstance(gap["criterion"], dict)
        assert gap["criterion"]["status"] == "ABSENT"

    def test_json_safe_output(self):
        d = matchresult_to_dict(_make_full_matchresult())
        payload = json.dumps(d, ensure_ascii=False)
        assert isinstance(payload, str)

    def test_empty_matchresult_serialises(self):
        mr = MatchResult(
            application_id="app-1",
            job_id="job-1",
            criteria_version="hash1",
        )
        d = matchresult_to_dict(mr)
        assert d["matches"] == []
        assert d["gap_candidates"] == []
        assert d["algorithmic_scores"] == {}

    def test_matcher_version_preserved(self):
        mr = MatchResult(
            application_id="a", job_id="j",
            criteria_version="h", matcher_version="3.1.0",
        )
        d = matchresult_to_dict(mr)
        assert d["matcher_version"] == "3.1.0"


# ── MatchResult — deserialisation ─────────────────────────────────────────────

class TestMatchResultFromDict:
    def test_round_trip_full(self):
        original = _make_full_matchresult()
        restored = matchresult_from_dict(matchresult_to_dict(original))
        assert dataclasses.asdict(restored) == dataclasses.asdict(original)

    def test_round_trip_empty(self):
        original = MatchResult(
            application_id="app-1", job_id="job-1", criteria_version="v1"
        )
        restored = matchresult_from_dict(matchresult_to_dict(original))
        assert restored.application_id == "app-1"
        assert restored.matches == []
        assert restored.algorithmic_scores == {}

    def test_round_trip_preserves_criterion_match_fields(self):
        cm = _make_criterion_match(
            criterion_text="Python",
            status="PARTIAL",
            confidence=0.45,
        )
        cm.partial_reason = "inferred from scripting background"
        cm.matched_via_translation = True
        cm.original_cv_term = "بايثون"
        mr = MatchResult(
            application_id="a", job_id="j",
            criteria_version="h", matches=[cm],
        )
        restored = matchresult_from_dict(matchresult_to_dict(mr))
        r_cm = restored.matches[0]
        assert r_cm.criterion_text == "Python"
        assert r_cm.status == "PARTIAL"
        assert r_cm.confidence == 0.45
        assert r_cm.partial_reason == "inferred from scripting background"
        assert r_cm.matched_via_translation is True
        assert r_cm.original_cv_term == "بايثون"

    def test_round_trip_preserves_gap_candidate_fields(self):
        gap = GapCandidate(
            criterion=_make_criterion_match(status="ABSENT", confidence=0.0),
            severity="SIGNIFICANT",
            compensating_evidence=["10 years related experience"],
            compensating_confidence=0.65,
            suppressed=False,
            suppression_reason="",
        )
        mr = MatchResult(
            application_id="a", job_id="j",
            criteria_version="h", gap_candidates=[gap],
        )
        restored = matchresult_from_dict(matchresult_to_dict(mr))
        r_gap = restored.gap_candidates[0]
        assert r_gap.severity == "SIGNIFICANT"
        assert r_gap.compensating_confidence == 0.65
        assert r_gap.compensating_evidence == ["10 years related experience"]

    def test_round_trip_suppressed_gap(self):
        gap = GapCandidate(
            criterion=_make_criterion_match(status="ABSENT", confidence=0.0),
            severity="MINOR",
            compensating_evidence=["Strong relevant background"],
            compensating_confidence=0.80,
            suppressed=True,
            suppression_reason="Strong experience compensates",
        )
        mr = MatchResult(
            application_id="a", job_id="j",
            criteria_version="h", gap_candidates=[gap],
        )
        restored = matchresult_from_dict(matchresult_to_dict(mr))
        r_gap = restored.gap_candidates[0]
        assert r_gap.suppressed is True
        assert r_gap.suppression_reason == "Strong experience compensates"

    def test_missing_keys_use_defaults(self):
        mr = matchresult_from_dict({
            "application_id": "app-1",
            "job_id": "job-1",
            "criteria_version": "hash",
        })
        assert mr.matches == []
        assert mr.blocking_gap_count == 0
        assert mr.matcher_version == "0.0.0"

    def test_completely_empty_dict_uses_defaults(self):
        mr = matchresult_from_dict({})
        assert mr.application_id == ""
        assert mr.matches == []
        assert mr.algorithmic_scores == {}

    def test_non_dict_raises_serialisation_error(self):
        for bad in (None, "string", 42, [], True):
            with pytest.raises(SerialisationError):
                matchresult_from_dict(bad)

    def test_malformed_match_item_skipped(self):
        d = matchresult_to_dict(_make_full_matchresult())
        d["matches"].append("not a dict")
        restored = matchresult_from_dict(d)
        assert len(restored.matches) == 2  # original 2 still present

    def test_gap_with_missing_criterion_gets_placeholder(self):
        d = {
            "application_id": "a",
            "job_id": "j",
            "criteria_version": "h",
            "gap_candidates": [
                {"severity": "BLOCKING", "suppressed": False}
                # no "criterion" key
            ],
        }
        mr = matchresult_from_dict(d)
        assert len(mr.gap_candidates) == 1
        # Should have a placeholder criterion, not raise
        assert mr.gap_candidates[0].criterion.criterion_text == ""
        assert mr.gap_candidates[0].criterion.status == "ABSENT"

    def test_algorithmic_scores_dict_restored(self):
        original = _make_full_matchresult()
        restored = matchresult_from_dict(matchresult_to_dict(original))
        assert restored.algorithmic_scores == {"skills": 50.0, "experience": 80.0}

    def test_matching_method_summary_restored(self):
        original = _make_full_matchresult()
        restored = matchresult_from_dict(matchresult_to_dict(original))
        assert restored.matching_method_summary == {"exact": 1, "absent": 1}

    def test_extra_unknown_keys_ignored(self):
        d = matchresult_to_dict(_make_full_matchresult())
        d["future_field"] = "some_value"
        restored = matchresult_from_dict(d)
        assert restored.application_id == "app-uuid-001"

    def test_multiple_gaps_all_restored(self):
        gaps = [_make_gap("BLOCKING"), _make_gap("SIGNIFICANT"), _make_gap("MINOR")]
        mr = MatchResult(
            application_id="a", job_id="j",
            criteria_version="h", gap_candidates=gaps,
        )
        restored = matchresult_from_dict(matchresult_to_dict(mr))
        assert len(restored.gap_candidates) == 3
        severities = [g.severity for g in restored.gap_candidates]
        assert "BLOCKING" in severities
        assert "SIGNIFICANT" in severities
        assert "MINOR" in severities


# ── Schema version tests ──────────────────────────────────────────────────────

class TestSchemaVersions:
    def test_cvfacts_schema_constant_is_string(self):
        assert isinstance(_CVFACTS_SCHEMA, str)
        assert len(_CVFACTS_SCHEMA) > 0

    def test_matchresult_schema_constant_is_string(self):
        assert isinstance(_MATCHRESULT_SCHEMA, str)
        assert len(_MATCHRESULT_SCHEMA) > 0

    def test_schema_key_not_interfere_with_from_dict(self):
        # _schema key is written to dict but ignored on read-back
        facts = CVFacts(language="en", total_char_count=100)
        d = cvfacts_to_dict(facts)
        assert "_schema" in d
        restored = cvfacts_from_dict(d)
        assert restored.language == "en"

    def test_schema_key_survives_json_serialisation(self):
        d = cvfacts_to_dict(CVFacts(language="en", total_char_count=0))
        payload = json.dumps(d)
        loaded = json.loads(payload)
        assert loaded["_schema"] == _CVFACTS_SCHEMA
