"""
E-01 Phase 2 — Bulk Upload Processor tests.

Covers pure-function logic in services/bulk_upload_processor.py:
  - ZIP metadata extraction
  - Excel parsing
  - Dynamic column mapping
  - Filename matching (exact / case-insensitive / stem)
  - Duplicate filename detection
  - Row-level validation (errors + warnings)
  - Candidate data + knockout answer building
  - ZIP summary computation

No database or HTTP layer required.
"""

import io
import zipfile

import openpyxl
import pytest

from services.bulk_upload_processor import (
    SUPPORTED_CV_EXTENSIONS,
    _cell_str,
    build_candidate_data,
    build_knockout_answers,
    compute_zip_summary,
    extract_zip_metadata,
    find_duplicate_filenames,
    map_columns,
    match_cv_files,
    parse_excel,
    validate_row,
)


# ── In-memory factory helpers ─────────────────────────────────────────────────

def _make_xlsx(*rows: list) -> bytes:
    """Create a minimal XLSX file in memory from a list of row lists."""
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _make_zip(*files: tuple[str, bytes]) -> bytes:
    """Create a minimal ZIP from (filename, content) pairs."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files:
            zf.writestr(name, content)
    return buf.getvalue()


def _zip_files(*filenames: str) -> list[dict]:
    """
    Build a zip_files list (as returned by extract_zip_metadata) from bare
    filenames, for use in matching / validation tests that don't need real ZIPs.
    """
    from pathlib import Path
    result = []
    for fn in filenames:
        ext = Path(fn).suffix.lower()
        result.append({
            "filename":        fn,
            "normalized_name": fn.lower(),
            "stem":            Path(fn).stem,
            "normalized_stem": Path(fn).stem.lower(),
            "extension":       ext,
            "size_bytes":      1024,
            "is_supported":    ext in SUPPORTED_CV_EXTENSIONS,
            "zip_path":        fn,
        })
    return result


# ── Full column mapping fixture ───────────────────────────────────────────────

_FULL_MAPPING = {
    "cv_filename_col": "CV File Name",
    "name_col":        "Name",
    "email_col":       "Email",
    "phone_col":       "Phone",
    "extra_cols":      [],
}

_MATCHED_RESULT = {
    "status":       "matched",
    "matched_file": {"filename": "john.pdf", "is_supported": True, "extension": ".pdf"},
    "match_type":   "exact",
    "all_matches":  [],
}


# ── TestCellStr ───────────────────────────────────────────────────────────────

class TestCellStr:
    def test_none_returns_empty(self):
        assert _cell_str(None) == ""

    def test_float_nan_returns_empty(self):
        import math
        assert _cell_str(float("nan")) == ""

    def test_whole_float_no_decimal(self):
        assert _cell_str(1.0) == "1"

    def test_string_stripped(self):
        assert _cell_str("  hello  ") == "hello"

    def test_integer_stringified(self):
        assert _cell_str(42) == "42"


# ── TestZipProcessing ─────────────────────────────────────────────────────────

class TestZipProcessing:
    def test_extracts_pdf(self):
        content = _make_zip(("john_doe.pdf", b"fake pdf"))
        files = extract_zip_metadata(content)
        assert len(files) == 1
        assert files[0]["filename"] == "john_doe.pdf"

    def test_multiple_files(self):
        content = _make_zip(("a.pdf", b""), ("b.docx", b""), ("c.doc", b""))
        files = extract_zip_metadata(content)
        assert len(files) == 3

    def test_supported_extensions(self):
        content = _make_zip(
            ("a.pdf", b""), ("b.docx", b""), ("c.doc", b""), ("d.txt", b""),
        )
        files = extract_zip_metadata(content)
        assert all(f["is_supported"] for f in files)

    def test_unsupported_extension_flagged(self):
        content = _make_zip(("resume.jpg", b""))
        files = extract_zip_metadata(content)
        assert len(files) == 1
        assert not files[0]["is_supported"]
        assert files[0]["extension"] == ".jpg"

    def test_nested_file_uses_basename(self):
        content = _make_zip(("subfolder/john_doe.pdf", b""))
        files = extract_zip_metadata(content)
        assert len(files) == 1
        assert files[0]["filename"] == "john_doe.pdf"

    def test_empty_zip_returns_empty_list(self):
        content = _make_zip()
        assert extract_zip_metadata(content) == []

    def test_invalid_bytes_raises(self):
        with pytest.raises(Exception):
            extract_zip_metadata(b"not a zip file at all")

    def test_filename_normalized_to_lowercase(self):
        content = _make_zip(("John Doe.PDF", b""))
        files = extract_zip_metadata(content)
        assert files[0]["normalized_name"] == "john doe.pdf"

    def test_extension_lowercased(self):
        content = _make_zip(("Resume.DOCX", b""))
        files = extract_zip_metadata(content)
        assert files[0]["extension"] == ".docx"

    def test_stem_extracted(self):
        content = _make_zip(("john_doe.pdf", b""))
        files = extract_zip_metadata(content)
        assert files[0]["stem"] == "john_doe"
        assert files[0]["normalized_stem"] == "john_doe"

    def test_size_bytes_recorded(self):
        content = _make_zip(("a.pdf", b"hello world"))
        files = extract_zip_metadata(content)
        assert files[0]["size_bytes"] == len(b"hello world")

    def test_mixed_supported_unsupported(self):
        content = _make_zip(("a.pdf", b""), ("b.png", b""), ("c.docx", b""))
        files = extract_zip_metadata(content)
        supported = [f for f in files if f["is_supported"]]
        unsupported = [f for f in files if not f["is_supported"]]
        assert len(supported) == 2
        assert len(unsupported) == 1


# ── TestExcelParsing ──────────────────────────────────────────────────────────

class TestExcelParsing:
    def test_basic_parsing(self):
        xlsx = _make_xlsx(
            ["CV File Name", "Name", "Email"],
            ["john.pdf", "John Doe", "john@example.com"],
        )
        headers, rows = parse_excel(xlsx)
        assert headers == ["CV File Name", "Name", "Email"]
        assert len(rows) == 1
        assert rows[0]["CV File Name"] == "john.pdf"
        assert rows[0]["_row_number"] == 1

    def test_row_numbers_start_at_1(self):
        xlsx = _make_xlsx(["CV File Name"], ["a.pdf"], ["b.pdf"])
        _, rows = parse_excel(xlsx)
        assert rows[0]["_row_number"] == 1
        assert rows[1]["_row_number"] == 2

    def test_empty_sheet_returns_empty(self):
        wb = openpyxl.Workbook()
        buf = io.BytesIO()
        wb.save(buf)
        headers, rows = parse_excel(buf.getvalue())
        assert headers == []
        assert rows == []

    def test_blank_rows_skipped(self):
        xlsx = _make_xlsx(
            ["CV File Name"],
            ["a.pdf"],
            [None],       # blank row
            ["b.pdf"],
        )
        _, rows = parse_excel(xlsx)
        assert len(rows) == 2
        values = [r["CV File Name"] for r in rows]
        assert "a.pdf" in values
        assert "b.pdf" in values

    def test_multiple_rows(self):
        xlsx = _make_xlsx(
            ["CV File Name", "Name"],
            ["a.pdf", "Alice"],
            ["b.pdf", "Bob"],
            ["c.pdf", "Carol"],
        )
        _, rows = parse_excel(xlsx)
        assert len(rows) == 3

    def test_header_whitespace_stripped(self):
        xlsx = _make_xlsx(["  CV File Name  ", " Name "], ["a.pdf", "Alice"])
        headers, _ = parse_excel(xlsx)
        assert "CV File Name" in headers
        assert "Name" in headers

    def test_empty_header_columns_ignored_in_rows(self):
        xlsx = _make_xlsx(["CV File Name", "", "Name"], ["a.pdf", "ignored", "Alice"])
        headers, rows = parse_excel(xlsx)
        assert "" not in rows[0]

    def test_header_only_sheet_returns_no_rows(self):
        xlsx = _make_xlsx(["CV File Name", "Name"])
        _, rows = parse_excel(xlsx)
        assert rows == []


# ── TestColumnMapping ─────────────────────────────────────────────────────────

class TestColumnMapping:
    def test_detects_cv_filename_col(self):
        m = map_columns(["CV File Name", "Name", "Email"])
        assert m["cv_filename_col"] == "CV File Name"

    def test_cv_detection_case_insensitive(self):
        m = map_columns(["cv file name"])
        assert m["cv_filename_col"] == "cv file name"

    def test_resume_alias(self):
        m = map_columns(["Resume"])
        assert m["cv_filename_col"] == "Resume"

    def test_filename_alias(self):
        m = map_columns(["Filename"])
        assert m["cv_filename_col"] == "Filename"

    def test_cv_alias(self):
        m = map_columns(["CV"])
        assert m["cv_filename_col"] == "CV"

    def test_name_col_detected(self):
        m = map_columns(["CV File Name", "Candidate Name"])
        assert m["name_col"] == "Candidate Name"

    def test_full_name_alias(self):
        m = map_columns(["CV File Name", "Full Name"])
        assert m["name_col"] == "Full Name"

    def test_email_col_detected(self):
        m = map_columns(["CV File Name", "Email Address"])
        assert m["email_col"] == "Email Address"

    def test_phone_col_detected(self):
        m = map_columns(["CV File Name", "Phone Number"])
        assert m["phone_col"] == "Phone Number"

    def test_mobile_alias(self):
        m = map_columns(["CV File Name", "Mobile"])
        assert m["phone_col"] == "Mobile"

    def test_extra_cols_captured(self):
        m = map_columns(["CV File Name", "Name", "Do you have a UAE visa?"])
        assert "Do you have a UAE visa?" in m["extra_cols"]

    def test_no_cv_col_returns_none(self):
        m = map_columns(["Name", "Email"])
        assert m["cv_filename_col"] is None

    def test_empty_headers_excluded_from_extra(self):
        m = map_columns(["CV File Name", "", "Email"])
        assert "" not in m["extra_cols"]

    def test_first_match_wins(self):
        m = map_columns(["CV File Name", "Filename"])
        assert m["cv_filename_col"] == "CV File Name"
        assert "Filename" in m["extra_cols"]

    def test_multiple_extra_cols_preserved(self):
        m = map_columns(["CV File Name", "Q1", "Q2", "Q3"])
        assert m["extra_cols"] == ["Q1", "Q2", "Q3"]

    def test_all_standard_cols_detected(self):
        m = map_columns(["CV File Name", "Full Name", "Email", "Phone"])
        assert m["cv_filename_col"] is not None
        assert m["name_col"] is not None
        assert m["email_col"] is not None
        assert m["phone_col"] is not None
        assert m["extra_cols"] == []


# ── TestFilenameMatching ──────────────────────────────────────────────────────

class TestFilenameMatching:
    def test_exact_match(self):
        zf = _zip_files("john_doe.pdf")
        r = match_cv_files("john_doe.pdf", zf)
        assert r["status"] == "matched"
        assert r["match_type"] == "exact"

    def test_case_insensitive_match(self):
        zf = _zip_files("John_Doe.PDF")
        r = match_cv_files("john_doe.pdf", zf)
        assert r["status"] == "matched"
        assert r["match_type"] == "case_insensitive"

    def test_stem_match_no_extension(self):
        zf = _zip_files("john_doe.pdf")
        r = match_cv_files("john_doe", zf)
        assert r["status"] == "matched"
        assert r["match_type"] == "no_extension"

    def test_not_found(self):
        zf = _zip_files("jane_smith.pdf")
        r = match_cv_files("john_doe.pdf", zf)
        assert r["status"] == "not_found"

    def test_empty_filename_string(self):
        zf = _zip_files("john.pdf")
        r = match_cv_files("", zf)
        assert r["status"] == "empty_filename"

    def test_none_filename(self):
        zf = _zip_files("john.pdf")
        r = match_cv_files(None, zf)
        assert r["status"] == "empty_filename"

    def test_whitespace_only_filename(self):
        zf = _zip_files("john.pdf")
        r = match_cv_files("   ", zf)
        assert r["status"] == "empty_filename"

    def test_multiple_exact_matches(self):
        zf = _zip_files("john.pdf", "john.pdf")
        r = match_cv_files("john.pdf", zf)
        assert r["status"] == "multiple_matches"

    def test_multiple_stem_matches(self):
        zf = _zip_files("john.pdf", "john.docx")
        r = match_cv_files("john", zf)
        assert r["status"] == "multiple_matches"

    def test_exact_preferred_over_case_insensitive(self):
        zf = _zip_files("John.pdf", "john.pdf")
        r = match_cv_files("john.pdf", zf)
        assert r["status"] == "matched"
        assert r["match_type"] == "exact"
        assert r["matched_file"]["filename"] == "john.pdf"

    def test_empty_zip_returns_not_found(self):
        r = match_cv_files("john.pdf", [])
        assert r["status"] == "not_found"

    def test_filename_with_spaces(self):
        zf = _zip_files("John Doe.pdf")
        r = match_cv_files("John Doe.pdf", zf)
        assert r["status"] == "matched"

    def test_all_matches_returned_for_multiple(self):
        zf = _zip_files("john.pdf", "john.docx")
        r = match_cv_files("john", zf)
        assert len(r["all_matches"]) == 2


# ── TestDuplicateDetection ────────────────────────────────────────────────────

class TestDuplicateDetection:
    def test_no_duplicates(self):
        rows = [{"CV File Name": "a.pdf"}, {"CV File Name": "b.pdf"}]
        assert find_duplicate_filenames(rows, "CV File Name") == set()

    def test_detects_duplicate(self):
        rows = [{"CV File Name": "john.pdf"}, {"CV File Name": "john.pdf"}]
        dups = find_duplicate_filenames(rows, "CV File Name")
        assert "john.pdf" in dups

    def test_triplicate_counted_once(self):
        rows = [{"CV File Name": "x.pdf"}] * 3
        dups = find_duplicate_filenames(rows, "CV File Name")
        assert len(dups) == 1

    def test_empty_filenames_excluded(self):
        rows = [{"CV File Name": ""}, {"CV File Name": ""}]
        dups = find_duplicate_filenames(rows, "CV File Name")
        assert dups == set()

    def test_no_cv_col_returns_empty(self):
        rows = [{"Name": "John"}]
        assert find_duplicate_filenames(rows, None) == set()

    def test_unique_filenames_no_dups(self):
        rows = [{"CV File Name": f"file{i}.pdf"} for i in range(10)]
        assert find_duplicate_filenames(rows, "CV File Name") == set()


# ── TestValidationRules ───────────────────────────────────────────────────────

class TestValidationRules:
    def test_clean_row_no_errors_no_warnings(self):
        row = {"CV File Name": "john.pdf", "Name": "John", "Email": "j@e.com", "Phone": "123"}
        errors, warns = validate_row(row, _FULL_MAPPING, _MATCHED_RESULT, set())
        assert errors == []
        assert warns == []

    # ── Critical errors ──────────────────────────────────────────────────────

    def test_empty_filename_is_error(self):
        row = {"CV File Name": "", "Name": "John", "Email": "j@e.com", "Phone": "123"}
        match = {"status": "empty_filename"}
        errors, _ = validate_row(row, _FULL_MAPPING, match, set())
        assert any("empty" in e.lower() for e in errors)

    def test_cv_not_found_is_error(self):
        row = {"CV File Name": "missing.pdf", "Name": "J", "Email": "e", "Phone": "p"}
        match = {"status": "not_found"}
        errors, _ = validate_row(row, _FULL_MAPPING, match, set())
        assert any("not found" in e.lower() for e in errors)

    def test_multiple_matches_is_error(self):
        row = {"CV File Name": "john.pdf", "Name": "J", "Email": "e", "Phone": "p"}
        match = {"status": "multiple_matches"}
        errors, _ = validate_row(row, _FULL_MAPPING, match, set())
        assert any("multiple" in e.lower() for e in errors)

    def test_duplicate_filename_is_error(self):
        row = {"CV File Name": "john.pdf", "Name": "J", "Email": "e", "Phone": "p"}
        errors, _ = validate_row(row, _FULL_MAPPING, _MATCHED_RESULT, {"john.pdf"})
        assert any("duplicate" in e.lower() for e in errors)

    def test_unsupported_file_type_is_error(self):
        row = {"CV File Name": "resume.jpg", "Name": "J", "Email": "e", "Phone": "p"}
        match = {
            "status":       "matched",
            "matched_file": {"filename": "resume.jpg", "is_supported": False, "extension": ".jpg"},
        }
        errors, _ = validate_row(row, _FULL_MAPPING, match, set())
        assert any("unsupported" in e.lower() for e in errors)

    # ── Warnings ─────────────────────────────────────────────────────────────

    def test_missing_name_is_warning(self):
        row = {"CV File Name": "j.pdf", "Name": "", "Email": "j@e.com", "Phone": "123"}
        _, warns = validate_row(row, _FULL_MAPPING, _MATCHED_RESULT, set())
        assert any("name" in w.lower() for w in warns)

    def test_missing_email_is_warning(self):
        row = {"CV File Name": "j.pdf", "Name": "John", "Email": "", "Phone": "123"}
        _, warns = validate_row(row, _FULL_MAPPING, _MATCHED_RESULT, set())
        assert any("email" in w.lower() for w in warns)

    def test_missing_phone_is_warning(self):
        row = {"CV File Name": "j.pdf", "Name": "John", "Email": "j@e.com", "Phone": ""}
        _, warns = validate_row(row, _FULL_MAPPING, _MATCHED_RESULT, set())
        assert any("phone" in w.lower() for w in warns)

    def test_no_name_col_in_mapping_gives_warning(self):
        mapping = {**_FULL_MAPPING, "name_col": None}
        row = {"CV File Name": "j.pdf"}
        _, warns = validate_row(row, mapping, _MATCHED_RESULT, set())
        assert any("name" in w.lower() for w in warns)

    def test_no_email_col_in_mapping_gives_warning(self):
        mapping = {**_FULL_MAPPING, "email_col": None}
        row = {"CV File Name": "j.pdf", "Name": "J", "Phone": "123"}
        _, warns = validate_row(row, mapping, _MATCHED_RESULT, set())
        assert any("email" in w.lower() for w in warns)

    def test_no_phone_col_in_mapping_gives_warning(self):
        mapping = {**_FULL_MAPPING, "phone_col": None}
        row = {"CV File Name": "j.pdf", "Name": "J", "Email": "j@e.com"}
        _, warns = validate_row(row, mapping, _MATCHED_RESULT, set())
        assert any("phone" in w.lower() for w in warns)

    def test_empty_extra_col_gives_warning(self):
        mapping = {**_FULL_MAPPING, "extra_cols": ["Do you have a UAE visa?"]}
        row = {
            "CV File Name": "j.pdf", "Name": "J", "Email": "e", "Phone": "p",
            "Do you have a UAE visa?": "",
        }
        _, warns = validate_row(row, mapping, _MATCHED_RESULT, set())
        assert any("UAE visa" in w for w in warns)

    def test_filled_extra_col_no_warning(self):
        mapping = {**_FULL_MAPPING, "extra_cols": ["Q1"]}
        row = {"CV File Name": "j.pdf", "Name": "J", "Email": "e", "Phone": "p", "Q1": "yes"}
        _, warns = validate_row(row, mapping, _MATCHED_RESULT, set())
        assert not any("Q1" in w for w in warns)

    def test_error_and_warning_can_coexist(self):
        row = {"CV File Name": "", "Name": ""}
        match = {"status": "empty_filename"}
        errors, warns = validate_row(row, _FULL_MAPPING, match, set())
        assert errors     # critical: empty filename
        assert warns      # warning: missing name etc.

    def test_no_cv_col_in_mapping_gives_error(self):
        mapping = {**_FULL_MAPPING, "cv_filename_col": None}
        row = {}
        match = {"status": "empty_filename"}
        errors, _ = validate_row(row, mapping, match, set())
        assert any("empty" in e.lower() for e in errors)


# ── TestCandidateDataBuilding ─────────────────────────────────────────────────

class TestCandidateDataBuilding:
    def test_known_fields_extracted(self):
        row = {"CV File Name": "j.pdf", "Name": "John", "Email": "j@e.com", "Phone": "555"}
        d = build_candidate_data(row, _FULL_MAPPING)
        assert d["name"] == "John"
        assert d["email"] == "j@e.com"
        assert d["phone"] == "555"

    def test_empty_value_excluded(self):
        row = {"CV File Name": "j.pdf", "Name": "", "Email": "j@e.com", "Phone": None}
        d = build_candidate_data(row, _FULL_MAPPING)
        assert "name" not in d
        assert "phone" not in d

    def test_extra_cols_not_in_candidate_data(self):
        mapping = {**_FULL_MAPPING, "extra_cols": ["KO Question"]}
        row = {"CV File Name": "j.pdf", "Name": "John", "KO Question": "yes"}
        d = build_candidate_data(row, mapping)
        assert "KO Question" not in d

    def test_no_optional_cols_gives_empty_dict(self):
        mapping = {**_FULL_MAPPING, "name_col": None, "email_col": None, "phone_col": None}
        row = {"CV File Name": "j.pdf"}
        d = build_candidate_data(row, mapping)
        assert d == {}


# ── TestKnockoutAnswerBuilding ────────────────────────────────────────────────

class TestKnockoutAnswerBuilding:
    def test_extra_cols_become_answers(self):
        mapping = {**_FULL_MAPPING, "extra_cols": ["Q1", "Q2"]}
        row = {"CV File Name": "j.pdf", "Q1": "yes", "Q2": "no"}
        ans = build_knockout_answers(row, mapping)
        assert ans["Q1"] == "yes"
        assert ans["Q2"] == "no"

    def test_empty_value_stored(self):
        mapping = {**_FULL_MAPPING, "extra_cols": ["Q1"]}
        row = {"CV File Name": "j.pdf", "Q1": ""}
        ans = build_knockout_answers(row, mapping)
        assert "Q1" in ans
        assert ans["Q1"] == ""

    def test_none_value_stored_as_empty(self):
        mapping = {**_FULL_MAPPING, "extra_cols": ["Q1"]}
        row = {"CV File Name": "j.pdf", "Q1": None}
        ans = build_knockout_answers(row, mapping)
        assert ans["Q1"] == ""

    def test_no_extra_cols_returns_empty_dict(self):
        row = {"CV File Name": "j.pdf"}
        ans = build_knockout_answers(row, _FULL_MAPPING)
        assert ans == {}

    def test_standard_cols_not_in_answers(self):
        mapping = {**_FULL_MAPPING, "extra_cols": ["KO"]}
        row = {"CV File Name": "j.pdf", "Name": "John", "Email": "j@e.com", "KO": "yes"}
        ans = build_knockout_answers(row, mapping)
        assert "Name" not in ans
        assert "Email" not in ans


# ── TestZipSummary ────────────────────────────────────────────────────────────

class TestZipSummary:
    def _zf(self, *fns: str) -> list[dict]:
        return _zip_files(*fns)

    def test_all_matched(self):
        zf = self._zf("a.pdf", "b.pdf")
        s = compute_zip_summary(zf, {"a.pdf", "b.pdf"})
        assert s["unmatched_cv_count"] == 0
        assert s["total_files"] == 2

    def test_unmatched_counted(self):
        zf = self._zf("a.pdf", "b.pdf", "c.pdf")
        s = compute_zip_summary(zf, {"a.pdf"})
        assert s["unmatched_cv_count"] == 2
        assert "b.pdf" in s["unmatched_cv_filenames"]
        assert "c.pdf" in s["unmatched_cv_filenames"]

    def test_empty_zip_all_zeros(self):
        s = compute_zip_summary([], set())
        assert s["total_files"] == 0
        assert s["unmatched_cv_count"] == 0

    def test_supported_vs_unsupported(self):
        zf = self._zf("a.pdf", "b.jpg")
        s = compute_zip_summary(zf, set())
        assert s["supported_files"] == 1
        assert s["unsupported_files"] == 1

    def test_case_insensitive_reference_matching(self):
        # referenced_filenames may use different case than ZIP
        zf = self._zf("John_Doe.pdf")
        # matched_file["filename"] is the ZIP's filename (exact case)
        s = compute_zip_summary(zf, {"John_Doe.pdf"})
        assert s["unmatched_cv_count"] == 0

    def test_unmatched_filenames_list_complete(self):
        zf = self._zf("a.pdf", "b.pdf", "c.pdf")
        s = compute_zip_summary(zf, {"a.pdf"})
        assert sorted(s["unmatched_cv_filenames"]) == ["b.pdf", "c.pdf"]


# ── TestEndToEndParsing ───────────────────────────────────────────────────────

class TestEndToEndParsing:
    """
    Integration smoke test: build a synthetic batch (no DB), run the full
    parse → map → match → validate → summarise pipeline in memory.
    """

    def _run(self, xlsx: bytes, zip_bytes: bytes) -> dict:
        zip_files = extract_zip_metadata(zip_bytes)
        headers, rows = parse_excel(xlsx)
        col_mapping = map_columns(headers)
        dup_fns = find_duplicate_filenames(rows, col_mapping["cv_filename_col"])
        referenced: set[str] = set()
        results = []
        for row in rows:
            fn    = _cell_str(row.get(col_mapping.get("cv_filename_col") or ""))
            match = match_cv_files(fn, zip_files)
            errs, warns = validate_row(row, col_mapping, match, dup_fns)
            if match["status"] == "matched":
                referenced.add(match["matched_file"]["filename"])
            vs = "error" if errs else ("warning" if warns else "ready")
            results.append({"row_number": row["_row_number"], "status": vs,
                             "errors": errs, "warnings": warns})
        summary = compute_zip_summary(zip_files, referenced)
        return {"rows": results, "summary": summary}

    def test_happy_path(self):
        xlsx = _make_xlsx(
            ["CV File Name", "Name", "Email", "Phone"],
            ["john.pdf",  "John",  "j@e.com", "111"],
            ["jane.docx", "Jane",  "n@e.com", "222"],
        )
        zips = _make_zip(("john.pdf", b""), ("jane.docx", b""))
        out = self._run(xlsx, zips)
        assert all(r["status"] == "ready" for r in out["rows"])
        assert out["summary"]["unmatched_cv_count"] == 0

    def test_missing_cv_gives_error(self):
        xlsx = _make_xlsx(
            ["CV File Name", "Name"],
            ["missing.pdf", "Alice"],
        )
        zips = _make_zip(("other.pdf", b""))
        out = self._run(xlsx, zips)
        assert out["rows"][0]["status"] == "error"
        assert out["summary"]["unmatched_cv_count"] == 1

    def test_duplicate_filename_in_spreadsheet(self):
        xlsx = _make_xlsx(
            ["CV File Name", "Name"],
            ["john.pdf", "John A"],
            ["john.pdf", "John B"],
        )
        zips = _make_zip(("john.pdf", b""))
        out = self._run(xlsx, zips)
        assert all(r["status"] == "error" for r in out["rows"])

    def test_warning_row_missing_email(self):
        xlsx = _make_xlsx(
            ["CV File Name", "Name"],
            ["jane.pdf", "Jane"],
        )
        zips = _make_zip(("jane.pdf", b""))
        out = self._run(xlsx, zips)
        # Missing email → warning (no email col at all)
        assert out["rows"][0]["status"] == "warning"
        assert any("email" in w.lower() for w in out["rows"][0]["warnings"])

    def test_unmatched_cvs_in_zip(self):
        xlsx = _make_xlsx(
            ["CV File Name", "Name", "Email", "Phone"],
            ["john.pdf", "John", "j@e.com", "1"],
        )
        zips = _make_zip(("john.pdf", b""), ("extra1.pdf", b""), ("extra2.pdf", b""))
        out = self._run(xlsx, zips)
        assert out["summary"]["unmatched_cv_count"] == 2
