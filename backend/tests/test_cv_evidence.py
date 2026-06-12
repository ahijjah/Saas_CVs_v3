"""
Unit tests for services/cv_evidence.py (Batch 2A-1).

Validates:
- object creation with required and optional fields
- default values
- type correctness
- serialisation-friendly structure (all fields JSON-serialisable)
- EDUCATION_LEVELS ordering invariant
"""
from __future__ import annotations

import dataclasses
import json
import sys
import os

# Allow importing from services/ without a full package install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from services.cv_evidence import (
    EDUCATION_LEVELS,
    CVFacts,
    CertificationEvidence,
    DomainSignal,
    EducationEvidence,
    ExperienceEvidence,
    SkillEvidence,
    SoftSkillSignal,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_json(obj) -> str:
    """Serialise a dataclass to JSON via its dict representation."""
    return json.dumps(dataclasses.asdict(obj))


# ── SkillEvidence ─────────────────────────────────────────────────────────────

class TestSkillEvidence:
    def test_explicit_skill_creation(self):
        skill = SkillEvidence(
            skill_name="Microsoft Excel",
            raw_text="Excel",
            explicit=True,
            confidence=0.95,
            context_snippet="Proficient in Excel and Word",
            language="en",
            section_hint="skills",
        )
        assert skill.skill_name == "Microsoft Excel"
        assert skill.explicit is True
        assert skill.confidence == 0.95
        assert skill.language == "en"
        assert skill.inference_basis == ""  # default

    def test_inferred_skill_creation(self):
        skill = SkillEvidence(
            skill_name="Microsoft Excel",
            raw_text="prepared monthly spreadsheet reports",
            explicit=False,
            confidence=0.35,
            context_snippet="prepared monthly spreadsheet reports for management",
            language="en",
            section_hint="experience",
            inference_basis="prepared spreadsheets → Microsoft Excel",
        )
        assert skill.explicit is False
        assert skill.inference_basis != ""

    def test_arabic_skill(self):
        skill = SkillEvidence(
            skill_name="Microsoft Excel",
            raw_text="إكسل",
            explicit=True,
            confidence=0.82,
            context_snippet="مهارات: إكسل، وورد",
            language="ar",
            section_hint="skills",
        )
        assert skill.language == "ar"
        assert skill.raw_text == "إكسل"

    def test_json_serialisable(self):
        skill = SkillEvidence(
            skill_name="Python",
            raw_text="Python 3",
            explicit=True,
            confidence=0.95,
            context_snippet="Python 3 development",
            language="en",
            section_hint="skills",
        )
        payload = _to_json(skill)
        data = json.loads(payload)
        assert data["skill_name"] == "Python"
        assert data["explicit"] is True
        assert isinstance(data["confidence"], float)

    def test_confidence_boundary_values(self):
        for conf in (0.0, 0.5, 1.0):
            s = SkillEvidence(
                skill_name="Excel", raw_text="Excel", explicit=True,
                confidence=conf, context_snippet="", language="en",
                section_hint="skills",
            )
            assert s.confidence == conf


# ── ExperienceEvidence ────────────────────────────────────────────────────────

class TestExperienceEvidence:
    def test_full_experience_entry(self):
        exp = ExperienceEvidence(
            employer="Acme Corp",
            role_title="Records Officer",
            years=3.5,
            responsibilities=["Managed filing systems", "Prepared reports"],
            domain_signals=["records management", "document control"],
            skill_signals=["Microsoft Excel", "SharePoint"],
            raw_text="Records Officer at Acme Corp (2020–2023)",
        )
        assert exp.employer == "Acme Corp"
        assert exp.years == 3.5
        assert len(exp.responsibilities) == 2
        assert "records management" in exp.domain_signals

    def test_defaults(self):
        exp = ExperienceEvidence(
            employer="Unnamed Company",
            role_title="Analyst",
            years=0.0,
        )
        assert exp.responsibilities == []
        assert exp.domain_signals == []
        assert exp.skill_signals == []
        assert exp.raw_text == ""

    def test_zero_years_when_dates_absent(self):
        exp = ExperienceEvidence(
            employer="Company X",
            role_title="Manager",
            years=0.0,
        )
        assert exp.years == 0.0

    def test_json_serialisable(self):
        exp = ExperienceEvidence(
            employer="Acme",
            role_title="Officer",
            years=2.0,
            responsibilities=["Task A"],
        )
        data = json.loads(_to_json(exp))
        assert data["employer"] == "Acme"
        assert isinstance(data["years"], float)
        assert isinstance(data["responsibilities"], list)


# ── EducationEvidence ─────────────────────────────────────────────────────────

class TestEducationEvidence:
    def test_bachelors_entry(self):
        edu = EducationEvidence(
            degree_level="Bachelor's",
            field_of_study="Business Administration",
            institution="State University",
            year=2018,
        )
        assert edu.degree_level == "Bachelor's"
        assert edu.year == 2018
        assert edu.raw_text == ""

    def test_none_year(self):
        edu = EducationEvidence(
            degree_level="Master's",
            field_of_study="Computer Science",
            institution="Tech University",
            year=None,
        )
        assert edu.year is None

    def test_all_levels_in_constant(self):
        valid_levels = {"None", "High School", "Diploma", "Associate",
                        "Bachelor's", "Master's", "PhD"}
        assert set(EDUCATION_LEVELS) == valid_levels

    def test_education_levels_ordering(self):
        # PhD must come after Master's, Master's after Bachelor's
        levels = list(EDUCATION_LEVELS)
        assert levels.index("None") < levels.index("High School")
        assert levels.index("Bachelor's") < levels.index("Master's")
        assert levels.index("Master's") < levels.index("PhD")

    def test_json_serialisable(self):
        edu = EducationEvidence(
            degree_level="PhD",
            field_of_study="Information Science",
            institution="Research University",
            year=2022,
        )
        data = json.loads(_to_json(edu))
        assert data["degree_level"] == "PhD"
        assert data["year"] == 2022


# ── CertificationEvidence ─────────────────────────────────────────────────────

class TestCertificationEvidence:
    def test_full_cert(self):
        cert = CertificationEvidence(
            name="PMP",
            raw_text="PMP – Project Management Professional (PMI, 2021)",
            issuer="PMI",
            year=2021,
        )
        assert cert.name == "PMP"
        assert cert.issuer == "PMI"
        assert cert.year == 2021

    def test_defaults(self):
        cert = CertificationEvidence(
            name="ISO 9001 Lead Auditor",
            raw_text="ISO 9001 Lead Auditor",
        )
        assert cert.issuer == ""
        assert cert.year is None

    def test_json_serialisable(self):
        cert = CertificationEvidence(
            name="AWS Solutions Architect",
            raw_text="AWS Solutions Architect – Associate",
            issuer="Amazon",
            year=2023,
        )
        data = json.loads(_to_json(cert))
        assert data["name"] == "AWS Solutions Architect"
        assert data["year"] == 2023


# ── SoftSkillSignal ───────────────────────────────────────────────────────────

class TestSoftSkillSignal:
    def test_leadership_signal(self):
        sig = SoftSkillSignal(
            soft_skill_category="leadership",
            evidence_phrase="led a cross-functional team of 8 members",
            confidence=0.80,
            inference_basis="led a team → leadership",
        )
        assert sig.soft_skill_category == "leadership"
        assert sig.confidence == 0.80

    def test_default_inference_basis(self):
        sig = SoftSkillSignal(
            soft_skill_category="communication",
            evidence_phrase="prepared board-level presentations",
            confidence=0.65,
        )
        assert sig.inference_basis == ""

    def test_json_serialisable(self):
        sig = SoftSkillSignal(
            soft_skill_category="teamwork",
            evidence_phrase="collaborated with 3 departments",
            confidence=0.70,
        )
        data = json.loads(_to_json(sig))
        assert data["soft_skill_category"] == "teamwork"
        assert isinstance(data["confidence"], float)


# ── DomainSignal ──────────────────────────────────────────────────────────────

class TestDomainSignal:
    def test_domain_signal_with_snippets(self):
        sig = DomainSignal(
            domain_term="records management",
            frequency=4,
            context_snippets=[
                "supervised records management for 3 branches",
                "implemented records management system",
            ],
        )
        assert sig.domain_term == "records management"
        assert sig.frequency == 4
        assert len(sig.context_snippets) == 2

    def test_defaults(self):
        sig = DomainSignal(domain_term="GDPR")
        assert sig.frequency == 1
        assert sig.context_snippets == []

    def test_json_serialisable(self):
        sig = DomainSignal(
            domain_term="ISO 9001",
            frequency=2,
            context_snippets=["ISO 9001 certified processes"],
        )
        data = json.loads(_to_json(sig))
        assert data["domain_term"] == "ISO 9001"
        assert data["frequency"] == 2


# ── CVFacts ───────────────────────────────────────────────────────────────────

class TestCVFacts:
    def _minimal_facts(self, **kwargs) -> CVFacts:
        defaults = dict(language="en", total_char_count=1500)
        defaults.update(kwargs)
        return CVFacts(**defaults)

    def test_minimal_creation(self):
        facts = self._minimal_facts()
        assert facts.language == "en"
        assert facts.total_char_count == 1500
        assert facts.skills == []
        assert facts.experience == []
        assert facts.education == []
        assert facts.certifications == []
        assert facts.soft_skill_signals == []
        assert facts.domain_signals == []

    def test_default_aggregates(self):
        facts = self._minimal_facts()
        assert facts.total_experience_years == 0.0
        assert facts.highest_education_level == "None"
        assert facts.skill_names_normalised == []

    def test_default_metadata(self):
        facts = self._minimal_facts()
        assert facts.extractor_version == "0.0.0"
        assert facts.extraction_method == "rule_based_v1"
        assert facts.extraction_warnings == []

    def test_arabic_language(self):
        facts = self._minimal_facts(language="ar")
        assert facts.language == "ar"

    def test_mixed_language(self):
        facts = self._minimal_facts(language="mixed")
        assert facts.language == "mixed"

    def test_with_skills(self):
        skill = SkillEvidence(
            skill_name="Python",
            raw_text="Python",
            explicit=True,
            confidence=0.95,
            context_snippet="Python developer",
            language="en",
            section_hint="skills",
        )
        facts = self._minimal_facts(
            skills=[skill],
            skill_names_normalised=["Python"],
        )
        assert len(facts.skills) == 1
        assert "Python" in facts.skill_names_normalised

    def test_with_experience(self):
        exp = ExperienceEvidence(
            employer="TechCo",
            role_title="Developer",
            years=5.0,
        )
        facts = self._minimal_facts(
            experience=[exp],
            total_experience_years=5.0,
        )
        assert facts.total_experience_years == 5.0
        assert facts.experience[0].employer == "TechCo"

    def test_with_education(self):
        edu = EducationEvidence(
            degree_level="Master's",
            field_of_study="Computer Science",
            institution="MIT",
            year=2015,
        )
        facts = self._minimal_facts(
            education=[edu],
            highest_education_level="Master's",
        )
        assert facts.highest_education_level == "Master's"

    def test_json_serialisable_minimal(self):
        facts = self._minimal_facts()
        payload = _to_json(facts)
        data = json.loads(payload)
        assert data["language"] == "en"
        assert data["total_char_count"] == 1500
        assert data["skills"] == []
        assert data["extractor_version"] == "0.0.0"

    def test_json_serialisable_full(self):
        skill = SkillEvidence(
            skill_name="Excel",
            raw_text="Excel",
            explicit=True,
            confidence=0.95,
            context_snippet="Excel proficiency",
            language="en",
            section_hint="skills",
        )
        cert = CertificationEvidence(
            name="PMP",
            raw_text="PMP",
            issuer="PMI",
            year=2020,
        )
        facts = CVFacts(
            language="en",
            total_char_count=2000,
            skills=[skill],
            certifications=[cert],
            total_experience_years=3.0,
            highest_education_level="Bachelor's",
            skill_names_normalised=["Excel"],
            extractor_version="1.0.0",
            extraction_method="rule_based_v1",
            extraction_warnings=["date unparseable in block 2"],
        )
        payload = _to_json(facts)
        data = json.loads(payload)
        assert len(data["skills"]) == 1
        assert data["skills"][0]["skill_name"] == "Excel"
        assert len(data["certifications"]) == 1
        assert data["total_experience_years"] == 3.0

    def test_dataclass_fields_present(self):
        field_names = {f.name for f in dataclasses.fields(CVFacts)}
        required = {
            "language", "total_char_count", "skills", "experience",
            "education", "certifications", "soft_skill_signals",
            "domain_signals", "total_experience_years",
            "highest_education_level", "skill_names_normalised",
            "extractor_version", "extraction_method", "extraction_warnings",
        }
        assert required.issubset(field_names)

    def test_extraction_warnings_list(self):
        facts = self._minimal_facts(
            extraction_warnings=["no skills section found", "date unparseable"]
        )
        assert len(facts.extraction_warnings) == 2
