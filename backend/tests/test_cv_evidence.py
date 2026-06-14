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
    CVFactsExtractor,
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


# ── CVFactsExtractor — Batch 2A-4 ────────────────────────────────────────────

# ---------------------------------------------------------------------------
# Synthetic CV fixtures
# ---------------------------------------------------------------------------

_EN_CV = """
John Smith
john@example.com

Summary:
Experienced software developer with 7 years in backend systems.
Delivered projects on time and led a team of 5 engineers.

Skills:
Python, Java, SQL, PostgreSQL, Docker, Microsoft Excel, Git, Scrum

Work Experience:
Senior Developer - TechCorp (2019 - 2023)
Led a team of 5 developers to build cloud-based solutions.
Collaborated with cross-functional teams on multiple projects.
Resolved complex technical issues and met deadlines consistently.

Software Engineer - StartupXYZ (2016 - 2019)
Developed Python microservices and REST APIs.

Education:
Bachelor's in Computer Science, State University, 2015

Certifications:
PMP Certification (2022)
AWS Certified Solutions Architect (2021)
"""

_AR_CV = """
الاسم: أحمد محمد
البريد الإلكتروني: ahmed@example.com

الملخص:
مهندس برمجيات ذو خبرة في تطوير التطبيقات وإدارة الفرق.

المهارات:
بايثون، إكسل، وورد، جافا، قواعد البيانات

الخبرة:
مطور أول - شركة تقنية (2018 - 2022)
أدار فريق من المطورين وطور حلول تقنية.
تواصل مع العملاء وقدم التقارير للإدارة.

التعليم:
بكالوريوس في علوم الحاسوب، جامعة الملك عبدالله، 2017

الشهادات:
شهادة PMP في إدارة المشاريع (2021)
"""

_MIXED_CV = """
Sara Al-Hassan

Skills:
Python, SQL, إكسل, Power BI, SharePoint

Experience:
Records Manager - Government Ministry (2015 - Present)
Managed records management system for 3000+ employees.
Implemented digitization project and ISO 15489 compliance.
Led team of 5 archivists and supervised filing system migration.

Education:
Master's in Information Science, 2014

Certifications:
ISO 9001 Lead Auditor
Certified Records Manager
"""

_NO_SKILLS_CV = """
Name: Anonymous
A person who exists but lists no technical skills whatsoever.
Graduated from somewhere in some year.
"""


class TestCVFactsExtractor:
    """Batch 2A-4: CVFactsExtractor integration tests."""

    @pytest.fixture(autouse=True)
    def extractor(self):
        self.ex = CVFactsExtractor()

    # ── Basic metadata ─────────────────────────────────────────────────────

    def test_extractor_version(self):
        facts = self.ex.extract(_EN_CV)
        assert facts.extractor_version == "1.2.0"

    def test_extraction_method(self):
        facts = self.ex.extract(_EN_CV)
        assert facts.extraction_method == "rule_based_v1"

    def test_total_char_count_positive(self):
        facts = self.ex.extract(_EN_CV)
        assert facts.total_char_count > 0

    def test_empty_cv_returns_warning(self):
        facts = self.ex.extract("")
        assert "empty cv text" in facts.extraction_warnings

    def test_empty_cv_char_count_zero(self):
        facts = self.ex.extract("")
        assert facts.total_char_count == 0

    def test_output_is_cvfacts(self):
        facts = self.ex.extract(_EN_CV)
        assert isinstance(facts, CVFacts)

    def test_output_json_serialisable(self):
        facts = self.ex.extract(_EN_CV)
        payload = json.dumps(dataclasses.asdict(facts))
        data = json.loads(payload)
        assert data["extractor_version"] == "1.2.0"

    # ── Language detection ─────────────────────────────────────────────────

    def test_english_cv_language(self):
        facts = self.ex.extract(_EN_CV)
        assert facts.language in ("en", "mixed")

    def test_arabic_cv_language(self):
        facts = self.ex.extract(_AR_CV)
        # Might be "ar" or "mixed" depending on langdetect
        assert facts.language in ("ar", "mixed")

    def test_whitespace_only_cv(self):
        facts = self.ex.extract("   \n\t  ")
        assert "empty cv text" in facts.extraction_warnings

    # ── Skill extraction ───────────────────────────────────────────────────

    def test_english_skills_extracted(self):
        facts = self.ex.extract(_EN_CV)
        names = facts.skill_names_normalised
        assert "Python" in names
        assert "SQL" in names

    def test_docker_extracted(self):
        facts = self.ex.extract(_EN_CV)
        assert "Docker" in facts.skill_names_normalised

    def test_excel_extracted(self):
        facts = self.ex.extract(_EN_CV)
        assert "Microsoft Excel" in facts.skill_names_normalised

    def test_skills_in_skills_section_are_explicit(self):
        facts = self.ex.extract(_EN_CV)
        python_skills = [s for s in facts.skills if s.skill_name == "Python"]
        assert python_skills, "Python not found"
        assert python_skills[0].explicit is True

    def test_skill_confidence_high_in_skills_section(self):
        facts = self.ex.extract(_EN_CV)
        python_skills = [s for s in facts.skills if s.skill_name == "Python"]
        assert python_skills, "Python not found"
        assert python_skills[0].confidence >= 0.85

    def test_arabic_skill_excel_maps_to_english(self):
        facts = self.ex.extract(_AR_CV)
        names = facts.skill_names_normalised
        assert "Microsoft Excel" in names

    def test_arabic_skill_python_maps_to_english(self):
        facts = self.ex.extract(_AR_CV)
        names = facts.skill_names_normalised
        assert "Python" in names

    def test_arabic_skill_word_maps_to_microsoft_word(self):
        facts = self.ex.extract(_AR_CV)
        names = facts.skill_names_normalised
        assert "Microsoft Word" in names

    def test_mixed_cv_contains_english_and_arabic_skills(self):
        facts = self.ex.extract(_MIXED_CV)
        names = facts.skill_names_normalised
        assert "Python" in names
        assert "Microsoft Excel" in names
        assert "Power BI" in names

    def test_skill_names_normalised_no_duplicates(self):
        facts = self.ex.extract(_EN_CV)
        assert len(facts.skill_names_normalised) == len(set(facts.skill_names_normalised))

    def test_no_skills_warning(self):
        facts = self.ex.extract(_NO_SKILLS_CV)
        assert "no skills extracted" in facts.extraction_warnings

    def test_inferred_skill_has_lower_confidence(self):
        cv = """
        Skills:
        (none listed)

        Experience:
        Software Engineer at TechCo (2018 - 2022)
        Developed and maintained Python microservices.
        Used PostgreSQL for data storage.
        """
        facts = self.ex.extract(cv)
        python_skills = [s for s in facts.skills if s.skill_name == "Python"]
        if python_skills:
            assert python_skills[0].confidence < 0.50

    # ── Education extraction ───────────────────────────────────────────────

    def test_bachelors_detected(self):
        facts = self.ex.extract(_EN_CV)
        levels = [e.degree_level for e in facts.education]
        assert "Bachelor's" in levels

    def test_masters_detected(self):
        facts = self.ex.extract(_MIXED_CV)
        levels = [e.degree_level for e in facts.education]
        assert "Master's" in levels

    def test_highest_education_level_bachelors(self):
        facts = self.ex.extract(_EN_CV)
        assert facts.highest_education_level == "Bachelor's"

    def test_highest_education_level_masters(self):
        facts = self.ex.extract(_MIXED_CV)
        assert facts.highest_education_level == "Master's"

    def test_arabic_bachelors_detected(self):
        facts = self.ex.extract(_AR_CV)
        levels = [e.degree_level for e in facts.education]
        assert "Bachelor's" in levels

    # ── Certification extraction ───────────────────────────────────────────

    def test_pmp_detected(self):
        facts = self.ex.extract(_EN_CV)
        cert_names = [c.name for c in facts.certifications]
        assert "PMP" in cert_names

    def test_aws_certified_detected(self):
        facts = self.ex.extract(_EN_CV)
        cert_names = [c.name for c in facts.certifications]
        assert "AWS Certified" in cert_names

    def test_iso_9001_detected(self):
        facts = self.ex.extract(_MIXED_CV)
        cert_names = [c.name for c in facts.certifications]
        assert "ISO 9001" in cert_names

    def test_certified_records_manager_detected(self):
        facts = self.ex.extract(_MIXED_CV)
        cert_names = [c.name for c in facts.certifications]
        assert "Certified Records Manager" in cert_names

    # ── Soft skill signals ─────────────────────────────────────────────────

    def test_leadership_signal_from_led_team(self):
        facts = self.ex.extract(_EN_CV)
        categories = [s.soft_skill_category for s in facts.soft_skill_signals]
        assert "leadership" in categories

    def test_teamwork_signal_from_collaborated(self):
        facts = self.ex.extract(_EN_CV)
        categories = [s.soft_skill_category for s in facts.soft_skill_signals]
        assert "teamwork" in categories

    def test_soft_skill_confidence_in_range(self):
        facts = self.ex.extract(_EN_CV)
        for sig in facts.soft_skill_signals:
            assert 0.0 < sig.confidence <= 1.0

    # ── Domain signals ─────────────────────────────────────────────────────

    def test_records_management_domain_signal(self):
        facts = self.ex.extract(_MIXED_CV)
        terms = [d.domain_term for d in facts.domain_signals]
        assert "records management" in terms

    def test_digitization_domain_signal(self):
        facts = self.ex.extract(_MIXED_CV)
        terms = [d.domain_term for d in facts.domain_signals]
        assert "digitization" in terms

    def test_domain_signal_frequency_positive(self):
        facts = self.ex.extract(_MIXED_CV)
        records = [d for d in facts.domain_signals if d.domain_term == "records management"]
        assert records
        assert records[0].frequency >= 1

    # ── Experience year extraction ─────────────────────────────────────────

    def test_experience_years_summed_from_date_ranges(self):
        # EN_CV has 2019-2023 (4 yrs) + 2016-2019 (3 yrs) = 7 yrs
        facts = self.ex.extract(_EN_CV)
        assert facts.total_experience_years >= 4.0

    def test_experience_present_included(self):
        # MIXED_CV has 2015 - Present
        facts = self.ex.extract(_MIXED_CV)
        assert facts.total_experience_years >= 5.0

    def test_experience_years_not_negative(self):
        facts = self.ex.extract(_EN_CV)
        assert facts.total_experience_years >= 0.0

    def test_experience_years_capped_at_50(self):
        facts = self.ex.extract(_EN_CV)
        assert facts.total_experience_years <= 50.0

    # ── Batch 2B-2: experience aggregation & explicit statements ──────────────

    def test_multi_job_experience_summed(self):
        cv = """
Work Experience:
Senior Analyst - Bank of Jordan (2010 - 2015)
Managed financial records and customer data.

Records Officer - Ministry of Finance (2015 - 2020)
Maintained document control systems.

Head of Records - National Archives (2020 - Present)
Led archiving and digitization initiatives.
"""
        facts = self.ex.extract(cv)
        # 5 + 5 + (current_year - 2020) ≥ 10 years
        assert facts.total_experience_years >= 10.0

    def test_explicit_10_years_experience_statement(self):
        cv = """
Summary:
A seasoned banking professional with 10 years experience in records management.

Skills:
Microsoft Excel, Word, Data Management
"""
        facts = self.ex.extract(cv)
        assert facts.total_experience_years >= 10.0

    def test_explicit_over_ten_years(self):
        cv = """
Profile:
Records management specialist with over 12 years of relevant experience
in banking and financial services.

Skills:
Microsoft Office, Reporting, Filing Systems
"""
        facts = self.ex.extract(cv)
        assert facts.total_experience_years >= 12.0

    def test_explicit_years_does_not_override_higher_sum(self):
        # If date ranges sum to more than explicit statement, keep the higher sum.
        cv = """
Summary:
Professional with 5 years experience in finance.

Work Experience:
Analyst - Bank A (2008 - 2014)
Manager - Bank B (2014 - 2020)
"""
        facts = self.ex.extract(cv)
        # Date ranges give 12 years; explicit says 5; max = 12
        assert facts.total_experience_years >= 10.0

    def test_arabic_date_range_with_arabic_separator(self):
        cv = """
الخبرة:
محاسب - بنك الأردن (2012 إلى 2018)
إدارة السجلات المالية والمستندات.
مسؤول سجلات - وزارة المالية (2018 حتى الآن)
"""
        facts = self.ex.extract(cv)
        # 2012-2018 = 6 years + (current - 2018) ≥ 6
        assert facts.total_experience_years >= 6.0

    def test_standalone_word_extracted_from_skills_section(self):
        cv = """
Skills:
Microsoft Excel, Word, PowerPoint, Data Entry, Reporting
"""
        facts = self.ex.extract(cv)
        assert "Microsoft Word" in facts.skill_names_normalised

    def test_standalone_word_not_matched_in_isolation_avoidance(self):
        # Ensure Word is detected even without "Microsoft" prefix
        cv = """
Skills:
Word, Excel, Outlook
"""
        facts = self.ex.extract(cv)
        assert "Microsoft Word" in facts.skill_names_normalised
        assert "Microsoft Excel" in facts.skill_names_normalised

    def test_confidentiality_domain_signal_detected(self):
        cv = """
Experience:
Records Officer - ABC Bank (2015 - 2022)
Ensured strict confidentiality of sensitive customer data.
Maintained information security protocols and compliance standards.
"""
        facts = self.ex.extract(cv)
        domain_terms = [d.domain_term for d in facts.domain_signals]
        assert "confidentiality" in domain_terms

    def test_non_disclosure_domain_signal_detected(self):
        cv = """
Professional bound by non-disclosure agreements and data protection laws.
Experience in compliance and information security management.
"""
        facts = self.ex.extract(cv)
        domain_terms = [d.domain_term for d in facts.domain_signals]
        assert "confidentiality" in domain_terms
