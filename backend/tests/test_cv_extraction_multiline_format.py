"""
Regression test for CV experience extraction bug: multi-line format (Title/Company/Location).

Bug: CVFactsExtractor was extracting role_title as location instead of the actual role.
Example: "Ramallah, Palestine" (location) captured as role_title instead of
"ICT Systems Support Officer" (actual role).

Root cause: Parser assumed only 2 lines (Title/Company) before date, but
Rami's CV has 3 lines (Title/Company/Location).

Fix: Added location detection to identify and skip location lines when extracting
role_title and employer from multi-line formats.
"""
import pytest
from services.cv_evidence import CVFactsExtractor


class TestCVExtractionMultilineFormat:
    """Test experience extraction for multi-line format: Title / Company / Location / Date."""

    def test_rami_yousef_three_line_format_extracts_correct_role_title(self):
        """
        Test extraction of Rami Yousef's CV with 3-line format per entry.

        Format per entry:
          Line 1: Role Title
          Line 2: Company/Employer
          Line 3: Location (city, country)
          Line 4: Date Range

        Bug was: role_title captured "Ramallah, Palestine" (location)
        Fix should: role_title = "ICT Systems Support Officer", employer = "Digital Solutions Consulting"
        """
        cv_text = """
ICT Systems Support Officer
Digital Solutions Consulting
Ramallah, Palestine
June 2024 – Present
Supported implementation and operational rollout of digital business services.

Junior Business Systems Analyst
Future Technology Solutions
Ramallah, Palestine
July 2023 – May 2024
Analyzed business requirements for enterprise systems.
"""

        extractor = CVFactsExtractor()
        cv_facts = extractor.extract(cv_text)

        # Should extract 2 experience entries
        assert len(cv_facts.experience) == 2, f"Expected 2 entries, got {len(cv_facts.experience)}"

        # First entry
        exp1 = cv_facts.experience[0]
        assert exp1.role_title == "ICT Systems Support Officer", (
            f"First entry role_title should be 'ICT Systems Support Officer', "
            f"got '{exp1.role_title}' (likely captured location instead)"
        )
        assert exp1.employer == "Digital Solutions Consulting", (
            f"First entry employer should be 'Digital Solutions Consulting', "
            f"got '{exp1.employer}'"
        )

        # Second entry
        exp2 = cv_facts.experience[1]
        assert exp2.role_title == "Junior Business Systems Analyst", (
            f"Second entry role_title should be 'Junior Business Systems Analyst', "
            f"got '{exp2.role_title}' (likely captured location instead)"
        )
        assert exp2.employer == "Future Technology Solutions", (
            f"Second entry employer should be 'Future Technology Solutions', "
            f"got '{exp2.employer}'"
        )

    def test_single_line_format_still_works(self):
        """
        Verify that single-line formats (all on one line) still work correctly
        after the multi-line format fix.
        """
        cv_text = """
Senior Software Engineer at ABC Technologies, Cairo
January 2020 – Present
Led development of enterprise systems.

Junior Developer at XYZ Corp
March 2019 – December 2019
Maintained backend APIs.
"""

        extractor = CVFactsExtractor()
        cv_facts = extractor.extract(cv_text)

        # Single-line format should still work
        assert len(cv_facts.experience) >= 1, "Should extract at least one entry"
        exp1 = cv_facts.experience[0]

        # For single-line format, role includes everything on that line
        assert "Senior Software Engineer" in exp1.role_title, (
            f"Role should contain 'Senior Software Engineer', got '{exp1.role_title}'"
        )

    def test_location_only_line_detection(self):
        """
        Verify that lines containing ONLY geographic info are detected as locations,
        but lines containing company info are NOT flagged as locations.
        """
        from services.cv_evidence import _is_likely_location_line

        # These should be detected as locations (pure geography)
        assert _is_likely_location_line("Ramallah, Palestine") is True
        assert _is_likely_location_line("Cairo, Egypt") is True
        assert _is_likely_location_line("London") is True

        # These should NOT be detected as locations (contain company/job info)
        assert _is_likely_location_line("ABC Company, Cairo") is False
        assert _is_likely_location_line("Tech Solutions Ltd") is False
        assert _is_likely_location_line("Senior Engineer at TechCorp") is False
        assert _is_likely_location_line("Human Resources Department") is False
