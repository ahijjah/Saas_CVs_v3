"""
E-01 Phase 3 — Bulk Upload Import tests.

Covers pure-function logic in services/bulk_upload_import_service.py:
  - Extension → MIME type mapping
  - Knockout answer matching (column header → question_id)
  - Row import eligibility rules
  - Batch terminal status determination
  - ZIP metadata lookup construction

No database or HTTP layer required.
"""

import io
import zipfile

import pytest

from services.bulk_upload_import_service import (
    determine_final_batch_status,
    ext_to_mime,
    is_row_eligible,
    load_cv_from_zip,
    match_answers_to_questions,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_zip_with_file(filename: str, content: bytes = b"fake cv content") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(filename, content)
    return buf.getvalue()


def _zip_meta_lookup(*filenames: str) -> dict:
    """
    Build the zip_meta_lookup dict (as produced by the import service) from
    bare filenames.  Uses filename as both filename and zip_path for simplicity.
    """
    from pathlib import Path
    lookup = {}
    for fn in filenames:
        ext = Path(fn).suffix.lower()
        entry = {
            "filename":        fn,
            "normalized_name": fn.lower(),
            "zip_path":        fn,   # no subdirectory in these test fixtures
            "extension":       ext,
        }
        lookup[fn]         = entry
        lookup[fn.lower()] = entry
    return lookup


def _questions(*pairs: tuple[str, str]) -> list[dict]:
    """Build a job_questions list from (question_id, question_text) pairs."""
    return [{"question_id": qid, "question_text": text} for qid, text in pairs]


# ── TestExtToMime ─────────────────────────────────────────────────────────────

class TestExtToMime:
    def test_pdf_returns_correct_mime(self):
        assert ext_to_mime(".pdf") == "application/pdf"

    def test_docx_returns_correct_mime(self):
        assert ext_to_mime(".docx") == (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

    def test_doc_returns_correct_mime(self):
        assert ext_to_mime(".doc") == "application/msword"

    def test_txt_returns_none(self):
        assert ext_to_mime(".txt") is None

    def test_jpg_returns_none(self):
        assert ext_to_mime(".jpg") is None

    def test_png_returns_none(self):
        assert ext_to_mime(".png") is None

    def test_uppercase_ext_handled(self):
        assert ext_to_mime(".PDF") == "application/pdf"

    def test_empty_ext_returns_none(self):
        assert ext_to_mime("") is None

    def test_unknown_ext_returns_none(self):
        assert ext_to_mime(".xls") is None


# ── TestMatchAnswersToQuestions ───────────────────────────────────────────────

_Q_ID_1 = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
_Q_ID_2 = "b2c3d4e5-f6a7-8901-bcde-f12345678901"


class TestMatchAnswersToQuestions:
    def test_exact_match(self):
        answers = {"Do you have a driving licence?": "yes"}
        questions = _questions((_Q_ID_1, "Do you have a driving licence?"))
        matched, unmatched = match_answers_to_questions(answers, questions)
        assert len(matched) == 1
        assert matched[0]["question_id"] == _Q_ID_1
        assert matched[0]["answer_value"] == "yes"
        assert unmatched == {}

    def test_case_insensitive_match(self):
        answers = {"do you have a driving licence?": "yes"}
        questions = _questions((_Q_ID_1, "Do you have a driving licence?"))
        matched, unmatched = match_answers_to_questions(answers, questions)
        assert len(matched) == 1
        assert matched[0]["question_id"] == _Q_ID_1

    def test_no_match_goes_to_unmatched(self):
        answers = {"Some unrelated column": "value"}
        questions = _questions((_Q_ID_1, "Do you have a driving licence?"))
        matched, unmatched = match_answers_to_questions(answers, questions)
        assert matched == []
        assert "Some unrelated column" in unmatched

    def test_mixed_matched_and_unmatched(self):
        answers = {
            "Do you have a driving licence?": "yes",
            "Unrecognised column":            "whatever",
        }
        questions = _questions((_Q_ID_1, "Do you have a driving licence?"))
        matched, unmatched = match_answers_to_questions(answers, questions)
        assert len(matched) == 1
        assert len(unmatched) == 1
        assert "Unrecognised column" in unmatched

    def test_multiple_questions_multiple_matches(self):
        answers = {
            "Q1 text": "yes",
            "Q2 text": "no",
        }
        questions = _questions((_Q_ID_1, "Q1 text"), (_Q_ID_2, "Q2 text"))
        matched, unmatched = match_answers_to_questions(answers, questions)
        assert len(matched) == 2
        assert unmatched == {}

    def test_empty_answers_returns_empty(self):
        questions = _questions((_Q_ID_1, "Some question"))
        matched, unmatched = match_answers_to_questions({}, questions)
        assert matched == []
        assert unmatched == {}

    def test_empty_questions_all_unmatched(self):
        answers = {"Column A": "answer"}
        matched, unmatched = match_answers_to_questions(answers, [])
        assert matched == []
        assert "Column A" in unmatched

    def test_none_value_stored_as_empty_string(self):
        answers = {"Q1 text": None}
        questions = _questions((_Q_ID_1, "Q1 text"))
        matched, _ = match_answers_to_questions(answers, questions)
        assert matched[0]["answer_value"] == ""

    def test_numeric_value_converted_to_string(self):
        answers = {"Years of experience": 5}
        questions = _questions((_Q_ID_1, "Years of experience"))
        matched, _ = match_answers_to_questions(answers, questions)
        assert matched[0]["answer_value"] == "5"

    def test_empty_header_skipped(self):
        answers = {"": "ignored", "Q1 text": "yes"}
        questions = _questions((_Q_ID_1, "Q1 text"))
        matched, unmatched = match_answers_to_questions(answers, questions)
        assert len(matched) == 1
        assert "" not in unmatched

    def test_exact_preferred_over_case_insensitive(self):
        # Both exact and lower-case match the same question
        answers = {"Q1 Text": "exact"}
        questions = _questions((_Q_ID_1, "Q1 Text"), (_Q_ID_2, "q1 text"))
        matched, _ = match_answers_to_questions(answers, questions)
        assert len(matched) == 1
        # Should prefer exact match → _Q_ID_1
        assert matched[0]["question_id"] == _Q_ID_1

    def test_none_answers_returns_empty(self):
        questions = _questions((_Q_ID_1, "Q1"))
        matched, unmatched = match_answers_to_questions(None, questions)
        assert matched == []
        assert unmatched == {}


# ── TestIsRowEligible ─────────────────────────────────────────────────────────

class TestIsRowEligible:
    def _row(self, vs: str) -> dict:
        return {"validation_status": vs}

    def test_ready_row_always_eligible(self):
        eligible, _ = is_row_eligible(self._row("ready"), include_warnings=True)
        assert eligible

    def test_ready_row_eligible_when_warnings_excluded(self):
        eligible, _ = is_row_eligible(self._row("ready"), include_warnings=False)
        assert eligible

    def test_warning_row_eligible_when_include_warnings_true(self):
        eligible, _ = is_row_eligible(self._row("warning"), include_warnings=True)
        assert eligible

    def test_warning_row_not_eligible_when_include_warnings_false(self):
        eligible, reason = is_row_eligible(self._row("warning"), include_warnings=False)
        assert not eligible
        assert "warning" in reason.lower()

    def test_error_row_never_eligible(self):
        eligible, reason = is_row_eligible(self._row("error"), include_warnings=True)
        assert not eligible
        assert "error" in reason.lower()

    def test_error_row_not_eligible_even_without_warning_filter(self):
        eligible, _ = is_row_eligible(self._row("error"), include_warnings=False)
        assert not eligible

    def test_pending_row_not_eligible(self):
        eligible, reason = is_row_eligible(self._row("pending"), include_warnings=True)
        assert not eligible
        assert "pending" in reason.lower()

    def test_missing_validation_status_defaults_to_pending(self):
        eligible, _ = is_row_eligible({}, include_warnings=True)
        assert not eligible

    def test_reason_empty_when_eligible(self):
        _, reason = is_row_eligible(self._row("ready"), include_warnings=True)
        assert reason == ""


# ── TestDetermineFinalBatchStatus ─────────────────────────────────────────────

class TestDetermineFinalBatchStatus:
    def test_some_imported_returns_imported(self):
        assert determine_final_batch_status(5, 2, 1, 0) == "imported"

    def test_only_already_imported_returns_imported(self):
        assert determine_final_batch_status(0, 0, 0, 10) == "imported"

    def test_mix_of_imported_and_failed_returns_imported(self):
        assert determine_final_batch_status(8, 0, 2, 0) == "imported"

    def test_all_failed_returns_failed(self):
        assert determine_final_batch_status(0, 0, 5, 0) == "failed"

    def test_all_skipped_returns_imported(self):
        # All eligible rows were skipped = clean run, treat as imported
        assert determine_final_batch_status(0, 10, 0, 0) == "imported"

    def test_zero_everything_returns_imported(self):
        assert determine_final_batch_status(0, 0, 0, 0) == "imported"

    def test_already_imported_plus_failed_returns_imported(self):
        # Already-imported takes precedence over failed
        assert determine_final_batch_status(0, 0, 3, 7) == "imported"


# ── TestLoadCvFromZip ─────────────────────────────────────────────────────────

class TestLoadCvFromZip:
    def _open_zip(self, content: bytes) -> "zipfile.ZipFile":
        return zipfile.ZipFile(io.BytesIO(content))

    def test_loads_pdf_successfully(self):
        zf_bytes = _make_zip_with_file("john.pdf", b"fake pdf")
        lookup = _zip_meta_lookup("john.pdf")
        with zipfile.ZipFile(io.BytesIO(zf_bytes)) as zf:
            content, mime = load_cv_from_zip(zf, lookup, "john.pdf")
        assert content == b"fake pdf"
        assert mime == "application/pdf"

    def test_loads_docx_successfully(self):
        zf_bytes = _make_zip_with_file("resume.docx", b"fake docx")
        lookup = _zip_meta_lookup("resume.docx")
        with zipfile.ZipFile(io.BytesIO(zf_bytes)) as zf:
            content, mime = load_cv_from_zip(zf, lookup, "resume.docx")
        assert mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    def test_case_insensitive_lookup(self):
        zf_bytes = _make_zip_with_file("John.PDF", b"pdf bytes")
        lookup = _zip_meta_lookup("John.PDF")
        with zipfile.ZipFile(io.BytesIO(zf_bytes)) as zf:
            content, mime = load_cv_from_zip(zf, lookup, "john.pdf")  # lowercase lookup
        assert content == b"pdf bytes"
        assert mime == "application/pdf"

    def test_missing_from_lookup_raises_value_error(self):
        zf_bytes = _make_zip_with_file("john.pdf", b"")
        lookup = {}  # empty lookup
        with zipfile.ZipFile(io.BytesIO(zf_bytes)) as zf:
            with pytest.raises(ValueError, match="not found in ZIP metadata"):
                load_cv_from_zip(zf, lookup, "john.pdf")

    def test_unsupported_extension_raises_value_error(self):
        zf_bytes = _make_zip_with_file("photo.jpg", b"jpg bytes")
        lookup = _zip_meta_lookup("photo.jpg")
        with zipfile.ZipFile(io.BytesIO(zf_bytes)) as zf:
            with pytest.raises(ValueError, match="Unsupported CV file type"):
                load_cv_from_zip(zf, lookup, "photo.jpg")

    def test_txt_raises_value_error(self):
        zf_bytes = _make_zip_with_file("cv.txt", b"plain text")
        lookup = _zip_meta_lookup("cv.txt")
        with zipfile.ZipFile(io.BytesIO(zf_bytes)) as zf:
            with pytest.raises(ValueError, match="Unsupported CV file type"):
                load_cv_from_zip(zf, lookup, "cv.txt")

    def test_file_in_zip_but_not_on_zip_path_raises(self):
        # Lookup points to wrong internal path
        zf_bytes = _make_zip_with_file("real.pdf", b"content")
        lookup = {
            "wrong.pdf": {"filename": "wrong.pdf", "normalized_name": "wrong.pdf",
                          "zip_path": "nonexistent.pdf", "extension": ".pdf"},
        }
        with zipfile.ZipFile(io.BytesIO(zf_bytes)) as zf:
            with pytest.raises(ValueError, match="missing from ZIP archive"):
                load_cv_from_zip(zf, lookup, "wrong.pdf")


# ── TestImportSummaryLogic ────────────────────────────────────────────────────

class TestImportSummaryLogic:
    """
    Simulate the counters that run_batch_import accumulates, checking the
    arithmetic is correct for the expected summary response.
    """

    def _simulate(self, rows: list[dict], include_warnings: bool = True) -> dict:
        imported = 0
        skipped = 0
        failed = 0
        already = 0
        for row in rows:
            if row.get("application_id"):
                already += 1
            elif row.get("import_status") == "failed":
                failed += 1
            else:
                eligible, _ = is_row_eligible(row, include_warnings)
                if eligible:
                    imported += 1
                else:
                    skipped += 1
        return {
            "imported": imported,
            "skipped": skipped,
            "failed": failed,
            "already_imported": already,
            "terminal_status": determine_final_batch_status(imported, skipped, failed, already),
        }

    def test_all_ready_rows(self):
        rows = [{"validation_status": "ready"} for _ in range(5)]
        r = self._simulate(rows)
        assert r["imported"] == 5
        assert r["skipped"] == 0
        assert r["terminal_status"] == "imported"

    def test_error_rows_skipped(self):
        rows = [{"validation_status": "error"} for _ in range(3)]
        r = self._simulate(rows)
        assert r["skipped"] == 3
        assert r["imported"] == 0
        assert r["terminal_status"] == "imported"

    def test_warning_rows_excluded_when_flag_false(self):
        rows = [{"validation_status": "warning"} for _ in range(3)]
        r = self._simulate(rows, include_warnings=False)
        assert r["skipped"] == 3
        assert r["imported"] == 0

    def test_warning_rows_included_when_flag_true(self):
        rows = [{"validation_status": "warning"} for _ in range(3)]
        r = self._simulate(rows, include_warnings=True)
        assert r["imported"] == 3

    def test_already_imported_not_re_imported(self):
        rows = [
            {"validation_status": "ready", "application_id": "some-uuid"},
        ]
        r = self._simulate(rows)
        assert r["already_imported"] == 1
        assert r["imported"] == 0
        assert r["terminal_status"] == "imported"

    def test_mixed_batch(self):
        rows = [
            {"validation_status": "ready"},                      # → imported
            {"validation_status": "ready"},                      # → imported
            {"validation_status": "warning"},                    # → imported (include_warnings=True)
            {"validation_status": "error"},                      # → skipped
            {"validation_status": "ready", "application_id": "x"},  # → already_imported
            {"validation_status": "ready", "import_status": "failed"},  # → failed
        ]
        r = self._simulate(rows, include_warnings=True)
        assert r["imported"] == 3
        assert r["skipped"] == 1
        assert r["already_imported"] == 1
        assert r["failed"] == 1
        assert r["terminal_status"] == "imported"

    def test_all_failed_gives_failed_status(self):
        rows = [
            {"validation_status": "ready", "import_status": "failed"},
            {"validation_status": "ready", "import_status": "failed"},
        ]
        r = self._simulate(rows)
        assert r["failed"] == 2
        assert r["terminal_status"] == "failed"


# ── TestKnockoutAnswerIntegration ─────────────────────────────────────────────

class TestKnockoutAnswerIntegration:
    """
    End-to-end pure-logic tests for the answer matching pipeline that
    happens during import.
    """

    def test_all_columns_matched(self):
        answers = {
            "Are you available immediately?": "yes",
            "Do you have a valid UAE visa?":  "no",
        }
        questions = _questions(
            (_Q_ID_1, "Are you available immediately?"),
            (_Q_ID_2, "Do you have a valid UAE visa?"),
        )
        matched, unmatched = match_answers_to_questions(answers, questions)
        assert len(matched) == 2
        assert unmatched == {}

    def test_partial_match(self):
        answers = {
            "Are you available immediately?": "yes",
            "Extra KO column not in system":  "maybe",
        }
        questions = _questions((_Q_ID_1, "Are you available immediately?"))
        matched, unmatched = match_answers_to_questions(answers, questions)
        assert len(matched) == 1
        assert "Extra KO column not in system" in unmatched

    def test_no_questions_for_job(self):
        """If job has no knockout questions, all answers are unmatched."""
        answers = {"Column A": "yes", "Column B": "no"}
        matched, unmatched = match_answers_to_questions(answers, [])
        assert matched == []
        assert len(unmatched) == 2

    def test_answer_values_preserved(self):
        answers = {"How many years experience?": "7"}
        questions = _questions((_Q_ID_1, "How many years experience?"))
        matched, _ = match_answers_to_questions(answers, questions)
        assert matched[0]["answer_value"] == "7"
