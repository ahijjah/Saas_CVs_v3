"""
Regression test for numeric MM/YYYY date range support.

Bug: CVFactsExtractor only recognized "Month Year" format dates (e.g. "November 2023"),
not numeric MM/YYYY format (e.g. "11/2023"). This caused complete extraction failure
for CVs using numeric dates - both experience entries AND total_experience_years came
back empty (0.0).

Real case: Shehab Abedrabuh's CV with pipe-delimited format:
  Technical Support
  Jawwal / Paltel - Palestine Telecommunications Company | 11/2023 - 11/2025 | Nablus, West Bank (Hybrid)

Before fix: experience[] empty, total_experience_years = 0.0
After fix: experience properly extracted, total_experience_years = 2.0
"""
import pytest
from services.cv_evidence import CVFactsExtractor


class TestNumericDateRangeExtraction:
    """Test extraction of MM/YYYY numeric date format."""

    def test_numeric_slash_separated_dates(self):
        """
        Test MM/YYYY date format with slash separator (e.g., "11/2023 - 11/2025").
        This is the real format from Shehab Abedrabuh's CV.
        """
        cv_text = """Technical Support
Jawwal / Paltel - Palestine Telecommunications Company | 11/2023 - 11/2025 | Nablus, West Bank
Resolved internet issues for XDSL, fiber-optic, and FWA connections."""

        extractor = CVFactsExtractor()
        cv_facts = extractor.extract(cv_text)

        # Should extract 1 experience entry
        assert len(cv_facts.experience) == 1
        exp = cv_facts.experience[0]

        # Verify extraction
        assert exp.role_title == "Technical Support"
        assert "Jawwal / Paltel" in exp.employer
        assert exp.years == 2.0, f"Expected 2 years (11/2023 to 11/2025), got {exp.years}"

        # Total years should be non-zero
        assert cv_facts.total_experience_years > 0, "total_experience_years should be > 0"
        assert cv_facts.total_experience_years == 2.0

    def test_numeric_dot_separated_dates(self):
        """
        Test MM.YYYY date format with dot separator (alternative European format).
        """
        cv_text = """Project Manager
Tech Solutions Inc
01.2020 - 06.2023
Managed software projects."""

        extractor = CVFactsExtractor()
        cv_facts = extractor.extract(cv_text)

        assert len(cv_facts.experience) == 1
        exp = cv_facts.experience[0]
        # Jan 2020 to June 2023 is 3 years + 5 months = 3.42 years (month-aware
        # fractional calc). The old assertion (3.0) encoded the pre-Bug-D bug
        # of integer year-only subtraction, not the correct duration.
        assert exp.years == 3.42, f"Expected 3.42 years (Jan 2020 to June 2023), got {exp.years}"
        assert cv_facts.total_experience_years > 0

    def test_mixed_date_formats_in_same_cv(self):
        """
        Test that numeric dates work alongside month-name dates in the same CV.
        """
        cv_text = """Senior Engineer
Company A
11/2023 - 12/2025
Engineered systems

Data Analyst
Company B
January 2021 – August 2023
Analyzed data"""

        extractor = CVFactsExtractor()
        cv_facts = extractor.extract(cv_text)

        # Should extract 2 entries (mixed formats)
        assert len(cv_facts.experience) == 2
        # Total should include both
        assert cv_facts.total_experience_years > 3.0

    def test_pipe_delimited_format_with_numeric_dates(self):
        """
        Test pipe-delimited format (Company | Dates | Location) with numeric dates.
        This is the specific real-world format that broke extraction completely.
        """
        cv_text = """Technical Support
Jawwal / Paltel - Palestine Telecommunications Company | 11/2023 - 11/2025 | Nablus, West Bank (Hybrid)
• Resolved internet issues
• Managed XDSL connections"""

        extractor = CVFactsExtractor()
        cv_facts = extractor.extract(cv_text)

        assert len(cv_facts.experience) == 1
        exp = cv_facts.experience[0]

        # Verify correct role title (should be "Technical Support", not the company or dates)
        assert exp.role_title == "Technical Support"
        # Verify employer extracted from before first pipe
        assert exp.employer == "Jawwal / Paltel - Palestine Telecommunications Company"
        # Verify years computed from numeric dates
        assert exp.years == 2.0
        assert cv_facts.total_experience_years == 2.0
