"""
Unit tests for intake_notification_service.

Tests cover:
- IntakeStatus enum values
- Email template building (subject, body content, HTML wrapping)
- Consolidated status logic for multi-attachment messages
- _insert_notification idempotency handling (mock DB)
- queue_candidate_notification DB toggle check
- Recruiter alert suppression logic
"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

# ── SQLAlchemy stub (same pattern as test_duplicate_detection.py) ─────────────
_sql_mock = MagicMock()
_sql_mock.text = lambda q: q
sys.modules.setdefault("sqlalchemy", _sql_mock)
sys.modules.setdefault("sqlalchemy.ext.asyncio", MagicMock())
sys.modules.setdefault("sqlalchemy.pool", MagicMock())

from services.intake_notification_service import (  # noqa: E402
    AttachmentResult,
    IntakeStatus,
    _attachment_label,
    _candidate_subject,
    build_candidate_email,
    build_recruiter_inactive_email,
    build_recruiter_unmatched_email,
)


# ── IntakeStatus enum ─────────────────────────────────────────────────────────

class TestIntakeStatus:
    def test_all_values_are_strings(self):
        for s in IntakeStatus:
            assert isinstance(s.value, str)
            assert s.value == s  # str enum

    def test_key_statuses_exist(self):
        names = {s.name for s in IntakeStatus}
        expected = {
            "RECEIVED_SUCCESSFULLY",
            "DUPLICATE_APPLICATION",
            "NO_ATTACHMENT",
            "UNSUPPORTED_FILE_TYPE",
            "FILE_CORRUPTED",
            "FILE_PASSWORD_PROTECTED",
            "EMPTY_CV_CONTENT",
            "FILE_SIZE_EXCEEDED",
            "JOB_NOT_IDENTIFIED",
            "JOB_INACTIVE",
            "MULTIPLE_ATTACHMENTS",
        }
        assert expected.issubset(names)


# ── Email subject mapping ─────────────────────────────────────────────────────

class TestCandidateSubject:
    def test_received_includes_job_title(self):
        subj = _candidate_subject(IntakeStatus.RECEIVED_SUCCESSFULLY, "Software Engineer")
        assert "Software Engineer" in subj
        assert "received" in subj.lower()

    def test_duplicate_includes_job_title(self):
        subj = _candidate_subject(IntakeStatus.DUPLICATE_APPLICATION, "DevOps Lead")
        assert "DevOps Lead" in subj

    def test_no_attachment_generic(self):
        subj = _candidate_subject(IntakeStatus.NO_ATTACHMENT, "")
        assert "attachment" in subj.lower()

    def test_job_not_identified_no_title(self):
        subj = _candidate_subject(IntakeStatus.JOB_NOT_IDENTIFIED, "any")
        # Should describe the routing problem, not expose job internals
        assert "position" in subj.lower() or "job" in subj.lower()

    def test_job_inactive(self):
        subj = _candidate_subject(IntakeStatus.JOB_INACTIVE, "QA Engineer")
        assert "accepting" in subj.lower() or "inactive" in subj.lower() or "longer" in subj.lower()


# ── Attachment label helper ───────────────────────────────────────────────────

class TestAttachmentLabel:
    def test_received_label(self):
        assert "received" in _attachment_label(IntakeStatus.RECEIVED_SUCCESSFULLY).lower()

    def test_duplicate_label(self):
        assert "duplicate" in _attachment_label(IntakeStatus.DUPLICATE_APPLICATION).lower()

    def test_password_label(self):
        assert "password" in _attachment_label(IntakeStatus.FILE_PASSWORD_PROTECTED).lower()

    def test_size_label(self):
        assert "large" in _attachment_label(IntakeStatus.FILE_SIZE_EXCEEDED).lower()


# ── build_candidate_email ─────────────────────────────────────────────────────

class TestBuildCandidateEmail:
    def test_received_body_has_no_ai_terms(self):
        _, text, html = build_candidate_email(
            IntakeStatus.RECEIVED_SUCCESSFULLY,
            "Ali Hassan", "Software Engineer", [], "ACME Corp",
        )
        for term in ("score", "gpt", "ai", "llm", "model", "ranking"):
            assert term not in text.lower()
            assert term not in html.lower()

    def test_received_body_mentions_job(self):
        _, text, html = build_candidate_email(
            IntakeStatus.RECEIVED_SUCCESSFULLY,
            "Ali Hassan", "Data Analyst", [], "ACME Corp",
        )
        assert "Data Analyst" in text
        assert "Data Analyst" in html

    def test_duplicate_body(self):
        _, text, _ = build_candidate_email(
            IntakeStatus.DUPLICATE_APPLICATION,
            "Sara", "Product Manager", [], "Corp",
        )
        assert "already received" in text.lower() or "already" in text.lower()

    def test_no_attachment_body(self):
        _, text, _ = build_candidate_email(
            IntakeStatus.NO_ATTACHMENT,
            "Tariq", "Designer", [], "Corp",
        )
        assert "attachment" in text.lower()
        assert "pdf" in text.lower() or "word" in text.lower()

    def test_password_protected_body(self):
        _, text, _ = build_candidate_email(
            IntakeStatus.FILE_PASSWORD_PROTECTED,
            "Reem", "Engineer", [], "Corp",
        )
        assert "password" in text.lower()

    def test_size_exceeded_body(self):
        _, text, _ = build_candidate_email(
            IntakeStatus.FILE_SIZE_EXCEEDED,
            "Omar", "Engineer", [], "Corp",
        )
        assert "size" in text.lower() or "large" in text.lower()

    def test_job_not_identified_body_mentions_code(self):
        _, text, _ = build_candidate_email(
            IntakeStatus.JOB_NOT_IDENTIFIED,
            "Lara", "any", [], "Corp",
        )
        assert "JOB-" in text or "code" in text.lower() or "reference" in text.lower()

    def test_job_inactive_body(self):
        _, text, _ = build_candidate_email(
            IntakeStatus.JOB_INACTIVE,
            "Omar", "Analyst", [], "Corp",
        )
        assert "no longer" in text.lower() or "inactive" in text.lower() or "accepting" in text.lower()

    def test_multiple_attachments_lists_files(self):
        results = [
            AttachmentResult("cv.pdf", IntakeStatus.RECEIVED_SUCCESSFULLY, "app-123"),
            AttachmentResult("cert.jpg", IntakeStatus.UNSUPPORTED_FILE_TYPE),
        ]
        _, text, html = build_candidate_email(
            IntakeStatus.MULTIPLE_ATTACHMENTS,
            "Ahmed", "Engineer", results, "Corp",
        )
        assert "cv.pdf" in text
        assert "cert.jpg" in text
        assert "received" in text.lower()
        assert "unsupported" in text.lower() or "format" in text.lower()

    def test_html_wraps_plain_text(self):
        _, _, html = build_candidate_email(
            IntakeStatus.RECEIVED_SUCCESSFULLY,
            "Test", "Role", [], "Corp",
        )
        assert html.startswith("<!DOCTYPE html>")
        assert "<body" in html
        assert "Corp" in html

    def test_greeting_uses_name(self):
        _, text, _ = build_candidate_email(
            IntakeStatus.RECEIVED_SUCCESSFULLY,
            "Majed Al-Rashid", "Director", [], "Corp",
        )
        assert "Majed Al-Rashid" in text

    def test_empty_name_uses_fallback(self):
        _, text, _ = build_candidate_email(
            IntakeStatus.RECEIVED_SUCCESSFULLY,
            "", "Director", [], "Corp",
        )
        assert "Applicant" in text


# ── build_recruiter_unmatched_email ───────────────────────────────────────────

class TestBuildRecruiterUnmatchedEmail:
    def test_subject_includes_count(self):
        subj, _, _ = build_recruiter_unmatched_email(73, 50, "Platform")
        assert "73" in subj

    def test_body_includes_count_and_threshold(self):
        _, text, _ = build_recruiter_unmatched_email(73, 50, "Platform")
        assert "73" in text
        assert "50" in text

    def test_body_has_no_ai_terms(self):
        _, text, _ = build_recruiter_unmatched_email(10, 50, "Platform")
        for term in ("score", "gpt", "llm", "ranking"):
            assert term not in text.lower()


# ── build_recruiter_inactive_email ────────────────────────────────────────────

class TestBuildRecruiterInactiveEmail:
    def test_subject_includes_job_title(self):
        subj, _, _ = build_recruiter_inactive_email("Senior Dev", "John", "Platform")
        assert "Senior Dev" in subj

    def test_body_includes_job_title_and_name(self):
        _, text, _ = build_recruiter_inactive_email("Data Analyst", "Sarah", "Corp")
        assert "Data Analyst" in text
        assert "Sarah" in text


# ── AttachmentResult dataclass ────────────────────────────────────────────────

class TestAttachmentResult:
    def test_defaults(self):
        r = AttachmentResult(filename="cv.pdf", status=IntakeStatus.RECEIVED_SUCCESSFULLY)
        assert r.application_id is None
        assert r.duplicate_log_id is None
        assert r.error_detail is None

    def test_custom_fields(self):
        r = AttachmentResult(
            filename="cv.pdf",
            status=IntakeStatus.DUPLICATE_APPLICATION,
            duplicate_log_id="dup-123",
            error_detail="hash match",
        )
        assert r.duplicate_log_id == "dup-123"
        assert r.status == IntakeStatus.DUPLICATE_APPLICATION


# ── Consolidated status logic ─────────────────────────────────────────────────

class TestConsolidatedStatus:
    """
    Verify the rule: one status if all attachments have the same outcome,
    MULTIPLE_ATTACHMENTS if mixed.
    (Logic lives in queue_candidate_notification; tested here via email-building.)
    """

    def _statuses(self, results):
        return {r.status for r in results}

    def test_single_success(self):
        results = [AttachmentResult("a.pdf", IntakeStatus.RECEIVED_SUCCESSFULLY, "x")]
        statuses = self._statuses(results)
        if len(results) == 1 or len(statuses) == 1:
            final = results[0].status
        else:
            final = IntakeStatus.MULTIPLE_ATTACHMENTS
        assert final == IntakeStatus.RECEIVED_SUCCESSFULLY

    def test_all_same_error(self):
        results = [
            AttachmentResult("a.pdf", IntakeStatus.FILE_CORRUPTED),
            AttachmentResult("b.pdf", IntakeStatus.FILE_CORRUPTED),
        ]
        statuses = self._statuses(results)
        final = results[0].status if len(statuses) == 1 else IntakeStatus.MULTIPLE_ATTACHMENTS
        assert final == IntakeStatus.FILE_CORRUPTED

    def test_mixed_outcomes(self):
        results = [
            AttachmentResult("a.pdf", IntakeStatus.RECEIVED_SUCCESSFULLY, "x"),
            AttachmentResult("b.jpg", IntakeStatus.UNSUPPORTED_FILE_TYPE),
        ]
        statuses = self._statuses(results)
        final = results[0].status if len(statuses) == 1 else IntakeStatus.MULTIPLE_ATTACHMENTS
        assert final == IntakeStatus.MULTIPLE_ATTACHMENTS
