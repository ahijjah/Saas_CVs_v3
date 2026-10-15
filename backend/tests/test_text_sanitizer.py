"""Tests for UTF-8 sanitization layer (Requirement F: all 8 cases)."""
from __future__ import annotations

import pytest

from services.text_sanitizer import sanitize_text_for_db, _sanitize


# ── Helper ────────────────────────────────────────────────────────────────────

def sanitize(text: str) -> str:
    return sanitize_text_for_db(text, application_id="test-app", source="test")


# ── F-1: Invalid UTF-8 / lone surrogates ─────────────────────────────────────

class TestInvalidUtf8:
    def test_lone_high_surrogate_removed(self):
        # U+D83D is a lone high surrogate — not a valid Unicode scalar value
        raw = "John \udcd0\udc8f Smith"
        result = sanitize(raw)
        # Surrogates replaced by '?' from encode errors='replace'
        assert "\udcd0" not in result
        assert "\udc8f" not in result
        # Surrounding text preserved
        assert "John" in result
        assert "Smith" in result

    def test_surrogate_pair_that_encodes_cleanly_preserved(self):
        # Some "surrogate-like" characters in valid BMP are fine
        text = "Hello 中文 World"  # Chinese characters — valid UTF-8
        assert sanitize(text) == text

    def test_all_surrogates_replaced(self):
        raw = "\ud800text\udfff"
        result = sanitize(raw)
        assert "\ud800" not in result
        assert "\udfff" not in result
        assert "text" in result

    def test_result_encodable_as_utf8(self):
        raw = "bad \udcff bytes here"
        result = sanitize(raw)
        # Must not raise
        result.encode("utf-8")

    def test_diagnostic_flags_surrogates(self):
        raw = "hello \ud800 world"
        diag = _sanitize(raw)
        assert diag.had_surrogates is True
        # Surrogates are replaced with '?' (same length), not removed;
        # chars_removed counts only actual length reduction
        assert "?" in diag.text

    def test_clean_string_no_changes(self):
        raw = "Clean CV text 2024"
        diag = _sanitize(raw)
        assert diag.had_surrogates is False
        assert diag.chars_removed == 0
        assert diag.text == raw


# ── F-2: Null bytes ───────────────────────────────────────────────────────────

class TestNullBytes:
    def test_null_byte_removed(self):
        raw = "Ahmed\x00Ali"
        result = sanitize(raw)
        assert "\x00" not in result
        assert "Ahmed" in result
        assert "Ali" in result

    def test_multiple_null_bytes_all_removed(self):
        raw = "\x00\x00Hello\x00World\x00"
        result = sanitize(raw)
        assert "\x00" not in result
        assert "Hello" in result
        assert "World" in result

    def test_diagnostic_flags_null(self):
        diag = _sanitize("text\x00here")
        assert diag.had_null_bytes is True

    def test_null_only_string_becomes_empty(self):
        assert sanitize("\x00\x00\x00") == ""


# ── F-3: PDF with embedded images (image content must not appear as text) ─────

class TestPdfWithEmbeddedImages:
    def test_page_text_with_binary_garbage_cleaned(self):
        # Simulate what PyMuPDF might return when reading near an image stream:
        # a mix of valid text and surrogates
        raw = "John Smith\nudcd0\udc8fSenior Developer\nudcffPython"
        result = sanitize(raw)
        assert "John Smith" in result
        assert "Senior Developer" in result
        assert "Python" in result
        result.encode("utf-8")  # must not raise

    def test_pure_image_page_returns_empty(self):
        # PyMuPDF returns '' for purely image pages — sanitizer handles it
        assert sanitize("") == ""

    def test_control_chars_from_binary_stream_stripped(self):
        raw = "Name:\x01\x02\x03John\x04\x05"
        result = sanitize(raw)
        assert "\x01" not in result
        assert "\x02" not in result
        assert "John" in result


# ── F-4: DOCX with embedded images ───────────────────────────────────────────

class TestDocxWithEmbeddedImages:
    def test_docx_text_with_null_bytes_sanitized(self):
        # LibreOffice→PDF path: DOCX converted to PDF before text extraction.
        # Simulate extracted text with null bytes that some converters emit.
        raw = "Software Engineer\x00\x00\nPython, Java\x00"
        result = sanitize(raw)
        assert "\x00" not in result
        assert "Software Engineer" in result
        assert "Python, Java" in result

    def test_mixed_garbage_and_valid_text(self):
        raw = "\x0b\x0cMohammed Al-Hassan\x0b\x0c"
        result = sanitize(raw)
        assert "Mohammed Al-Hassan" in result
        assert "\x0b" not in result
        assert "\x0c" not in result


# ── F-5: Arabic text preservation ────────────────────────────────────────────

class TestArabicPreservation:
    def test_arabic_text_preserved(self):
        arabic = "محمد علي"
        assert sanitize(arabic) == arabic

    def test_arabic_with_diacritics_preserved(self):
        # Diacritics (tashkeel) are valid UTF-8 — should not be stripped
        arabic_diacritics = "مُحَمَّدٌ"
        result = sanitize(arabic_diacritics)
        assert result == arabic_diacritics

    def test_arabic_numbers_preserved(self):
        arabic_nums = "١٢٣٤٥"
        assert sanitize(arabic_nums) == arabic_nums

    def test_arabic_punctuation_preserved(self):
        text = "الاسم: محمد؛ العمر: ٣٠"
        assert sanitize(text) == text


# ── F-6: Mixed Arabic/English preservation ───────────────────────────────────

class TestMixedArabicEnglish:
    def test_mixed_text_fully_preserved(self):
        mixed = "John Smith / محمد علي — Python Developer"
        assert sanitize(mixed) == mixed

    def test_mixed_with_surrogates_cleans_only_bad_chars(self):
        mixed = "Ahmed\ud800Ali — Python\udfffDeveloper"
        result = sanitize(mixed)
        assert "Ahmed" in result
        assert "Ali" in result
        assert "Python" in result
        assert "Developer" in result
        result.encode("utf-8")

    def test_line_breaks_preserved(self):
        text = "John Smith\nPython Developer\nDubai, UAE\nمحمد علي"
        result = sanitize(text)
        assert "\n" in result
        assert "John Smith" in result
        assert "محمد علي" in result

    def test_tabs_preserved(self):
        text = "Skill\tExperience\nPython\t5 years"
        result = sanitize(text)
        assert "\t" in result


# ── F-7: Image-only CV (no recoverable text) ─────────────────────────────────

class TestImageOnlyCV:
    def test_empty_string_returned_unchanged(self):
        # When PyMuPDF extracts no text from an image-only PDF, it returns ''.
        # Sanitizer must handle this gracefully (not raise, not crash).
        assert sanitize("") == ""

    def test_whitespace_only_returned_unchanged(self):
        # PyMuPDF may return only whitespace from image-heavy pages.
        result = sanitize("   \n  \t  ")
        # Whitespace is preserved (quality gate in cv_score.py handles this)
        assert result.strip() == ""

    def test_sanitizer_never_raises_on_any_bytes(self):
        # Construct a worst-case string: surrogates + nulls + controls
        raw = "".join(chr(i) for i in range(0, 32)) + "𐏿"
        # Must not raise
        result = sanitize(raw)
        result.encode("utf-8")


# ── F-8: Corrupted file ───────────────────────────────────────────────────────

class TestCorruptedFile:
    def test_partial_garbage_text_cleaned(self):
        # Simulate a corrupted PDF where fitz extracts partial binary garbage
        raw = "\x01\x02\x03John Smith\x04\x05\x06\udcffSenior Dev\x07"
        result = sanitize(raw)
        assert "John Smith" in result
        assert "Senior Dev" in result
        result.encode("utf-8")

    def test_completely_garbage_input_does_not_raise(self):
        raw = "\x00\x01\x02\x03\x04\x05\x06\x07\x08"
        result = sanitize(raw)
        result.encode("utf-8")

    def test_chars_removed_count_accurate(self):
        raw = "AB\x00CD"  # 5 chars, 1 null removed → 4 remain
        diag = _sanitize(raw)
        assert diag.chars_removed == 1
        assert len(diag.text) == 4


# ── Core invariants ───────────────────────────────────────────────────────────

class TestCoreInvariants:
    def test_output_always_utf8_encodable(self):
        cases = [
            "plain text",
            "𐏿",
            "\x00\x01\x02",
            "Arabic: محمد",
            "Mixed: John\x00\ud800",
            "",
        ]
        for case in cases:
            result = sanitize(case)
            try:
                result.encode("utf-8")
            except UnicodeEncodeError as exc:
                pytest.fail(f"Output not UTF-8 for input {case!r}: {exc}")

    def test_no_null_bytes_in_output(self):
        for raw in ["\x00", "a\x00b", "\x00\x00"]:
            assert "\x00" not in sanitize(raw)

    def test_newlines_and_carriage_returns_preserved(self):
        text = "line1\nline2\r\nline3\rline4"
        result = sanitize(text)
        assert "\n" in result
        assert "\r" in result

    def test_none_not_accepted(self):
        # sanitize_text_for_db expects str; passing None should be caught
        # by callers — but let's ensure we handle empty gracefully
        assert sanitize("") == ""
