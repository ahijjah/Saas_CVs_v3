"""
Regression test for Issue #19: False-negative in years-of-experience relevance check.

Bug: "Minimum X years of relevant experience" criterion incorrectly returns PARTIAL
when candidate has sufficient years and OTHER role-matching criteria confirm relevance,
but the years-criterion's isolated relevance check doesn't recognize the match
(often due to missing keywords like "ict" in domain_keywords list).

Real case: Rami Yousef (app fb8873f8) has 3 years of ICT Systems Support experience,
meets all role-matching criteria (MATCHED), but years criterion gets PARTIAL (0.45 confidence)
due to relevance check failure.
"""
import pytest
from services.criteria_matcher import _match_experience
from services.cv_evidence import CVFacts, ExperienceEvidence


class TestIssue19RelevanceCheckICT:
    """Test relevance check for ICT-related roles and keywords."""

    def test_ict_systems_support_matches_ict_relevant_experience(self):
        """
        POSITIVE CASE: Candidate has directly relevant ICT experience.
        Job requires: "Minimum 1 years of relevant experience"
        Candidate has: ~3 years as "ICT Systems Support Officer" and "Junior Business Systems Analyst"

        Expected: MATCHED (or at worst PARTIAL with high confidence)
        Bug was: PARTIAL with confidence 0.45 (false-negative)
        """
        cv_facts = CVFacts(
            language="en",
            total_char_count=500,
            total_experience_years=3.0,
            experience=[
                ExperienceEvidence(
                    role_title="ICT Systems Support Officer",
                    employer="Ministry of Communications",
                    years=1.5,
                    responsibilities=[
                        "Provide technical support for ICT systems",
                        "Manage system configurations and updates",
                    ],
                ),
                ExperienceEvidence(
                    role_title="Junior Business Systems Analyst",
                    employer="Tech Solutions Ltd",
                    years=1.5,
                    responsibilities=[
                        "Analyze business requirements for ICT systems",
                        "Coordinate with stakeholders on system implementations",
                    ],
                ),
            ],
        )

        # Job criteria with relevant_roles populated (as extract_job_criteria would produce)
        criteria = {
            "experience": {
                "minimum_years": 1,
                "relevant_roles": [
                    "ICT systems support",
                    "Business systems analyst",
                    "ICT support officer",
                ],
                "key_responsibilities": [
                    "technical support",
                    "system configuration",
                ],
            },
        }

        matches = _match_experience(cv_facts, criteria)
        assert len(matches) == 1, "Should produce one match for years criterion"

        match = matches[0]
        assert match.status in ["MATCHED", "PARTIAL"], (
            f"Status should be MATCHED or PARTIAL (was getting false-negative PARTIAL before fix). "
            f"Got: {match.status} with confidence {match.confidence}"
        )
        # Before fix: status="PARTIAL", confidence=0.45 (too low)
        # After fix: status should be MATCHED or at least PARTIAL with high confidence
        if match.status == "PARTIAL":
            assert (
                match.confidence >= 0.60
            ), f"If PARTIAL, confidence should be >= 0.60, got {match.confidence}"

    def test_genuinely_unrelated_experience_gets_partial(self):
        """
        NEGATIVE CASE: Candidate has unrelated experience.
        Job requires: "Minimum 1 years of relevant ICT experience"
        Candidate has: 5 years as "Restaurant Manager" (no ICT background)

        Expected: PARTIAL with low confidence or relevance-gap message
        """
        cv_facts = CVFacts(
            language="en",
            total_char_count=300,
            total_experience_years=5.0,
            experience=[
                ExperienceEvidence(
                    role_title="Restaurant Manager",
                    employer="Food Services Inc",
                    years=5.0,
                    responsibilities=[
                        "Manage daily restaurant operations",
                        "Train staff",
                    ],
                ),
            ],
        )

        criteria = {
            "experience": {
                "minimum_years": 1,
                "relevant_roles": ["ICT systems support", "Business systems analyst"],
                "key_responsibilities": ["technical support", "system configuration"],
            },
        }

        matches = _match_experience(cv_facts, criteria)
        assert len(matches) == 1

        match = matches[0]
        # Should still be PARTIAL (has years), but with low confidence due to relevance gap
        assert match.status == "PARTIAL"
        assert match.confidence <= 0.50, (
            f"Unrelated experience should have low confidence. Got {match.confidence}"
        )
        assert "relevance" in match.partial_reason.lower() or any(
            "not verified" in ev.lower() for ev in match.supporting_evidence
        ), "Should indicate relevance was not confirmed"

    def test_years_with_empty_relevant_roles(self):
        """
        EDGE CASE: Job has no relevant_roles/key_responsibilities specified.
        Expected: Fall back to pure numeric check → MATCHED if years sufficient.
        """
        cv_facts = CVFacts(
            language="en",
            total_char_count=200,
            total_experience_years=3.0,
            experience=[
                ExperienceEvidence(
                    role_title="Some role",
                    employer="Some company",
                    years=3.0,
                    responsibilities=[],
                ),
            ],
        )

        criteria = {
            "experience": {
                "minimum_years": 1,
                # No relevant_roles or key_responsibilities
            },
        }

        matches = _match_experience(cv_facts, criteria)
        assert len(matches) == 1

        match = matches[0]
        # Should be MATCHED because pure numeric check passes (3 >= 1)
        # and there's no relevance data to contradict it
        assert match.status == "MATCHED", (
            f"Without relevance data, should use pure numeric check. Got {match.status}"
        )
