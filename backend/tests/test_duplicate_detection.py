"""
Unit tests for duplicate_detection service.

Tests cover the normalisation helpers and the detection logic directly,
without a real database (mocked AsyncSession).
"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Stub out sqlalchemy so the service can be imported without it installed
_sql_mock = MagicMock()
_sql_mock.text = lambda q: q  # text() just returns the string
sys.modules.setdefault("sqlalchemy", _sql_mock)

from services.duplicate_detection import (  # noqa: E402
    _normalise_cv_text,
    _normalise_email,
    _normalise_phone,
    detect_possible_duplicate,
)


# ── Normalisation tests ───────────────────────────────────────────────────────

class TestNormaliseCvText:
    def test_lowercases(self):
        assert _normalise_cv_text("Python DEVELOPER") == "python developer"

    def test_strips_bullets(self):
        result = _normalise_cv_text("• Python • Java ▪ SQL")
        assert "•" not in result
        assert "python" in result

    def test_collapses_whitespace(self):
        result = _normalise_cv_text("hello   \n\n   world")
        assert result == "hello world"

    def test_empty_string(self):
        assert _normalise_cv_text("") == ""

    def test_none_like_empty(self):
        # Function expects str; empty str is falsy guard
        assert _normalise_cv_text("  ") == ""

    def test_preserves_arabic(self):
        result = _normalise_cv_text("مطور برمجيات Python")
        assert "مطور" in result
        assert "python" in result


class TestNormaliseEmail:
    def test_lowercases_and_strips(self):
        assert _normalise_email("  John@Example.COM  ") == "john@example.com"

    def test_none_returns_empty(self):
        assert _normalise_email(None) == ""

    def test_empty_returns_empty(self):
        assert _normalise_email("") == ""


class TestNormalisePhone:
    def test_strips_symbols(self):
        assert _normalise_phone("+971-50-123-4567") == "97150123-4567".replace("-", "")

    def test_strips_spaces(self):
        assert _normalise_phone("050 123 4567") == "501234567"

    def test_strips_leading_double_zero(self):
        assert _normalise_phone("0097150123456") == "97150123456"

    def test_strips_leading_zero(self):
        assert _normalise_phone("050 1234567") == "501234567"

    def test_none_returns_empty(self):
        assert _normalise_phone(None) == ""

    def test_empty_returns_empty(self):
        assert _normalise_phone("") == ""


# ── Integration-style mock tests ──────────────────────────────────────────────

LONG_CV = (
    "john smith software engineer python django rest api postgresql redis "
    "celery docker kubernetes aws five years experience bachelor computer science "
    "university of london agile scrum leadership communication problem solving "
) * 30  # ~2700 chars, representative length


def _make_mock_db(existing_rows: list[dict], current_reason: str | None = None):
    """
    Return an AsyncMock DB session.

    execute() call order:
      1. SELECT duplicate_reason FROM applications  → current_reason
      2. SELECT existing sibling apps               → existing_rows
      3. UPDATE applications SET duplicate_*        → MagicMock
    """
    db = AsyncMock()

    cur_result = MagicMock()
    cur_result.mappings.return_value.first.return_value = (
        {"duplicate_reason": current_reason} if current_reason is not None else None
    )

    sibling_result = MagicMock()
    sibling_result.mappings.return_value.all.return_value = existing_rows

    update_result = MagicMock()
    db.execute = AsyncMock(side_effect=[cur_result, sibling_result, update_result])
    return db


@pytest.mark.asyncio
async def test_same_cv_pdf_vs_docx_detected():
    """Layer 1: same content with minor formatting differences → possible_duplicate."""
    # Simulate slight extraction differences between PDF and DOCX
    cv_a = LONG_CV
    cv_b = LONG_CV.replace("john smith", "john  smith").replace("python", "Python")

    existing = [{
        "application_id": "aaaaaaaa-0000-0000-0000-000000000001",
        "candidate_name": "John Smith",
        "candidate_email": "other@example.com",
        "candidate_phone_from_cv": None,
        "extracted_text": cv_b,
    }]
    db = _make_mock_db(existing)

    with patch("services.duplicate_detection._mark_checked", new_callable=AsyncMock) as mock_mark:
        await detect_possible_duplicate(
            db=db,
            application_id="bbbbbbbb-0000-0000-0000-000000000002",
            job_id="job-1",
            tenant_id="tenant-1",
            candidate_name="John Smith",
            candidate_email="john@example.com",
            candidate_phone=None,
            extracted_text=cv_a,
        )
        mock_mark.assert_called_once()
        args = mock_mark.call_args[0]
        assert args[2] == "possible_duplicate"
        assert args[5] == "high_content_similarity"


@pytest.mark.asyncio
async def test_same_email_and_phone_no_cv_match_detected():
    """Layer 2: same email + same phone (2/3 fields) → possible_duplicate."""
    existing = [{
        "application_id": "aaaaaaaa-0000-0000-0000-000000000001",
        "candidate_name": "Different Person",
        "candidate_email": "alice@example.com",
        # Same local number, different punctuation — both normalise to "501234567"
        "candidate_phone_from_cv": "050-123-4567",
        "extracted_text": None,  # no text for Layer 1
    }]
    db = _make_mock_db(existing)

    with patch("services.duplicate_detection._mark_checked", new_callable=AsyncMock) as mock_mark:
        await detect_possible_duplicate(
            db=db,
            application_id="new-app-id",
            job_id="job-1",
            tenant_id="tenant-1",
            candidate_name="Alice Johnson",
            candidate_email="alice@example.com",
            candidate_phone="(050) 123 4567",  # same digits after normalisation
            extracted_text="",
        )
        mock_mark.assert_called_once()
        args = mock_mark.call_args[0]
        assert args[2] == "possible_duplicate"
        assert args[5] == "identity_match"


@pytest.mark.asyncio
async def test_same_name_only_not_duplicate():
    """Layer 2: only name matches — below 2-field threshold → not_duplicate."""
    existing = [{
        "application_id": "aaaaaaaa-0000-0000-0000-000000000001",
        "candidate_name": "John Smith",
        "candidate_email": "other@example.com",
        "candidate_phone_from_cv": "0501111111",
        "extracted_text": None,
    }]
    db = _make_mock_db(existing)

    with patch("services.duplicate_detection._mark_checked", new_callable=AsyncMock) as mock_mark:
        await detect_possible_duplicate(
            db=db,
            application_id="new-app-id",
            job_id="job-1",
            tenant_id="tenant-1",
            candidate_name="John Smith",      # same name
            candidate_email="john@company.com",  # different email
            candidate_phone="0502222222",         # different phone
            extracted_text="",
        )
        mock_mark.assert_called_once()
        args = mock_mark.call_args[0]
        assert args[2] == "not_duplicate"


@pytest.mark.asyncio
async def test_same_email_only_not_duplicate():
    """Layer 2: only email matches — below 2-field threshold → not_duplicate."""
    existing = [{
        "application_id": "aaaaaaaa-0000-0000-0000-000000000001",
        "candidate_name": "Completely Different Name",
        "candidate_email": "shared@example.com",
        "candidate_phone_from_cv": "0501111111",
        "extracted_text": None,
    }]
    db = _make_mock_db(existing)

    with patch("services.duplicate_detection._mark_checked", new_callable=AsyncMock) as mock_mark:
        await detect_possible_duplicate(
            db=db,
            application_id="new-app-id",
            job_id="job-1",
            tenant_id="tenant-1",
            candidate_name="John Smith",
            candidate_email="shared@example.com",  # same email
            candidate_phone="0502222222",           # different phone
            extracted_text="",
        )
        mock_mark.assert_called_once()
        args = mock_mark.call_args[0]
        assert args[2] == "not_duplicate"


@pytest.mark.asyncio
async def test_different_jobs_not_compared():
    """Different job_id means no rows returned → not_duplicate (empty existing)."""
    db = _make_mock_db([])  # no existing rows for this job

    with patch("services.duplicate_detection._mark_checked", new_callable=AsyncMock) as mock_mark:
        await detect_possible_duplicate(
            db=db,
            application_id="new-app-id",
            job_id="job-2",  # different job
            tenant_id="tenant-1",
            candidate_name="John Smith",
            candidate_email="john@example.com",
            candidate_phone="0501234567",
            extracted_text=LONG_CV,
        )
        mock_mark.assert_called_once()
        args = mock_mark.call_args[0]
        assert args[2] == "not_duplicate"


@pytest.mark.asyncio
async def test_no_self_comparison():
    """The application must not compare against itself (excluded in SQL WHERE)."""
    # The mock returns empty because the SQL excludes the current application_id.
    # Here we verify the function handles empty gracefully.
    db = _make_mock_db([])

    with patch("services.duplicate_detection._mark_checked", new_callable=AsyncMock) as mock_mark:
        await detect_possible_duplicate(
            db=db,
            application_id="self-id",
            job_id="job-1",
            tenant_id="tenant-1",
            candidate_name="John Smith",
            candidate_email="john@example.com",
            candidate_phone=None,
            extracted_text=LONG_CV,
        )
        args = mock_mark.call_args[0]
        assert args[2] == "not_duplicate"


@pytest.mark.asyncio
async def test_template_cv_below_threshold_not_duplicate():
    """Different candidates with similar CV templates should not be flagged.

    Each CV must have enough unique content that token_sort_ratio stays below 95%.
    Here the shared template is ~40% of each CV; the rest is candidate-specific.
    """
    shared_header = (
        "objective to obtain a challenging position summary of qualifications "
        "strong communication skills team player detail oriented "
        "education bachelor of science "
    )

    alice_body = (
        "alice johnson marketing manager five years digital marketing brand strategy "
        "content creation social media seo sem google ads facebook instagram "
        "campaign performance analytics a-b testing conversion rate optimisation "
        "led a team of eight creatives across dubai and riyadh "
        "increased organic traffic by 120 percent in 18 months "
        "managed budget of aed 2.5 million across three product lines "
        "awarded best campaign middle east 2023 marketing excellence awards "
    ) * 4

    bob_body = (
        "bob williams senior accountant cpa certified chartered accountant "
        "financial reporting audit compliance tax planning ifrs gaap "
        "prepared consolidated financial statements for multinational group "
        "managed external audit relationship with big four firm "
        "reduced tax liability by 15 percent through legitimate structuring "
        "implemented erp migration from sap to oracle financials "
        "eight years experience in banking and real estate sectors "
        "fluent in arabic english french proficient in excel power bi "
    ) * 4

    cv_a = shared_header + alice_body
    cv_b = shared_header + bob_body

    existing = [{
        "application_id": "aaaaaaaa-0000-0000-0000-000000000001",
        "candidate_name": "Bob Williams",
        "candidate_email": "bob@example.com",
        "candidate_phone_from_cv": None,
        "extracted_text": cv_b,
    }]
    db = _make_mock_db(existing)

    with patch("services.duplicate_detection._mark_checked", new_callable=AsyncMock) as mock_mark:
        await detect_possible_duplicate(
            db=db,
            application_id="new-app-id",
            job_id="job-1",
            tenant_id="tenant-1",
            candidate_name="Alice Johnson",
            candidate_email="alice@example.com",
            candidate_phone=None,
            extracted_text=cv_a,
        )
        args = mock_mark.call_args[0]
        assert args[2] == "not_duplicate"


@pytest.mark.asyncio
async def test_high_content_not_overwritten_by_identity_match():
    """
    Priority: if application already has high_content_similarity, a later run
    that only finds identity_match must NOT overwrite it.
    """
    # Simulate second run: no extracted text available (already done), but
    # phone is now known. current_reason = high_content_similarity from first run.
    existing = [{
        "application_id": "aaaaaaaa-0000-0000-0000-000000000001",
        "candidate_name": "John Smith",
        "candidate_email": "john@example.com",
        "candidate_phone_from_cv": "050-111-2222",
        "extracted_text": None,  # text not available in this run
    }]
    db = _make_mock_db(existing, current_reason="high_content_similarity")

    with patch("services.duplicate_detection._mark_checked", new_callable=AsyncMock) as mock_mark:
        await detect_possible_duplicate(
            db=db,
            application_id="app-under-test",
            job_id="job-1",
            tenant_id="tenant-1",
            candidate_name="John Smith",
            candidate_email="john@example.com",
            candidate_phone="0501112222",  # same number — would trigger identity_match
            extracted_text="",
        )
        # _mark_checked must NOT have been called — function returns early
        mock_mark.assert_not_called()


@pytest.mark.asyncio
async def test_identity_match_upgraded_to_content_similarity():
    """
    Priority: if application has identity_match from first run, a later run
    that finds high_content_similarity must upgrade it.
    """
    cv_a = LONG_CV
    cv_b = LONG_CV.replace("python", "Python")  # trivial formatting diff

    existing = [{
        "application_id": "aaaaaaaa-0000-0000-0000-000000000001",
        "candidate_name": "John Smith",
        "candidate_email": "other@example.com",
        "candidate_phone_from_cv": None,
        "extracted_text": cv_b,
    }]
    # current_reason = identity_match (set by first run, which had no text)
    db = _make_mock_db(existing, current_reason="identity_match")

    with patch("services.duplicate_detection._mark_checked", new_callable=AsyncMock) as mock_mark:
        await detect_possible_duplicate(
            db=db,
            application_id="app-under-test",
            job_id="job-1",
            tenant_id="tenant-1",
            candidate_name="John Smith",
            candidate_email="john@example.com",
            candidate_phone=None,
            extracted_text=cv_a,
        )
        mock_mark.assert_called_once()
        args = mock_mark.call_args[0]
        assert args[2] == "possible_duplicate"
        assert args[5] == "high_content_similarity"
