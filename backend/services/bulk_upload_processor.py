"""
E-01 Phase 2 — Bulk Upload Processor.

Pure-function module for ZIP parsing, Excel parsing, dynamic column mapping,
CV filename matching, and per-row validation.  No database or HTTP imports.

All functions are synchronous so they can be called from async route handlers
or from tests without an event loop.

Column mapping model
────────────────────
Required:   CV File Name  (many aliases accepted)
Optional:   Candidate Name, Email, Phone  (many aliases accepted)
Extra cols: everything else → stored in knockout_answers JSONB as
            {column_header: raw_value} for later question-matching phases.

Matching sequence (per row)
───────────────────────────
1. Exact filename match
2. Case-insensitive filename match
3. Stem match (ignore extension)

Validation categories
─────────────────────
Errors  (block import): empty filename, not found in ZIP, multiple matches,
                        unsupported file type, duplicate filename in spreadsheet
Warnings (allow import): missing name / email / phone, empty extra column value
"""

import io
import math
import zipfile
from pathlib import Path

import openpyxl

# ── Supported CV file types ───────────────────────────────────────────────────

SUPPORTED_CV_EXTENSIONS = frozenset({".pdf", ".docx", ".doc", ".txt"})

# ── Column alias tables ───────────────────────────────────────────────────────

_CV_FILENAME_ALIASES = frozenset({
    "cv file name", "cv filename", "cv_filename", "cv_file_name",
    "resume", "resume file", "resume filename", "resume file name",
    "cv", "file name", "filename", "file_name",
    "attachment", "cv attachment", "cv file",
})

_NAME_ALIASES = frozenset({
    "candidate name", "full name", "name", "applicant name", "candidate",
    "first name", "firstname", "last name", "lastname", "full_name",
    "candidate_name", "applicant", "person name", "person_name",
})

_EMAIL_ALIASES = frozenset({
    "email", "email address", "candidate email", "e-mail", "e_mail",
    "applicant email", "email_address", "candidate_email",
})

_PHONE_ALIASES = frozenset({
    "phone", "phone number", "mobile", "mobile number", "telephone",
    "contact number", "candidate phone", "phone_number", "mobile_number",
    "candidate_phone", "contact",
})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cell_str(val) -> str:
    """Safely stringify an openpyxl cell value (handles None, NaN, float int)."""
    if val is None:
        return ""
    if isinstance(val, float):
        if math.isnan(val):
            return ""
        # Avoid "1.0" for whole-number floats from Excel
        if val == int(val):
            return str(int(val))
    return str(val).strip()


# ── ZIP processing ────────────────────────────────────────────────────────────

def extract_zip_metadata(zip_content: bytes) -> list[dict]:
    """
    Read a ZIP archive and return metadata for every file inside it.

    Does NOT load file contents — only reads the ZIP central directory,
    making it efficient for large archives.

    Returns list of {
        filename, normalized_name, stem, normalized_stem,
        extension, size_bytes, is_supported, zip_path
    }
    Raises zipfile.BadZipFile if the bytes are not a valid ZIP.
    """
    files: list[dict] = []
    with zipfile.ZipFile(io.BytesIO(zip_content)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            # Use only the basename — strip directory paths inside the archive
            name = Path(info.filename).name
            if not name:
                continue
            ext = Path(name).suffix.lower()
            files.append({
                "filename":        name,
                "normalized_name": name.lower(),
                "stem":            Path(name).stem,
                "normalized_stem": Path(name).stem.lower(),
                "extension":       ext,
                "size_bytes":      info.file_size,
                "is_supported":    ext in SUPPORTED_CV_EXTENSIONS,
                "zip_path":        info.filename,
            })
    return files


# ── Excel parsing ─────────────────────────────────────────────────────────────

def parse_excel(excel_content: bytes) -> tuple[list[str], list[dict]]:
    """
    Parse an XLSX file and return (headers, data_rows).

    headers: list of column header strings (whitespace-stripped).
    data_rows: list of {column_name: value, '_row_number': int} dicts.
        '_row_number' is 1-indexed from the first data row (row after header).
        Entirely blank rows are skipped.

    Returns ([], []) for an empty or header-only sheet.
    Raises openpyxl exceptions for corrupt / non-XLSX content.
    """
    wb = openpyxl.load_workbook(io.BytesIO(excel_content), data_only=True, read_only=True)
    ws = wb.active

    rows_iter = ws.iter_rows(values_only=True)
    try:
        raw_headers = next(rows_iter)
    except StopIteration:
        wb.close()
        return [], []

    headers: list[str] = [
        str(h).strip() if h is not None else ""
        for h in raw_headers
    ]

    data_rows: list[dict] = []
    for row_number, row_values in enumerate(rows_iter, start=1):
        if all(v is None or (isinstance(v, str) and not v.strip()) for v in row_values):
            continue  # skip entirely blank rows
        row_dict: dict = {"_row_number": row_number}
        for header, value in zip(headers, row_values):
            if header:
                row_dict[header] = value
        data_rows.append(row_dict)

    wb.close()
    return headers, data_rows


# ── Column mapping ────────────────────────────────────────────────────────────

def map_columns(headers: list[str]) -> dict:
    """
    Detect standard field columns by matching headers against known aliases.

    First-match wins for each field type; duplicates go to extra_cols.
    Headers that are empty or purely whitespace are silently skipped.

    Returns {
        "cv_filename_col": str | None,
        "name_col":        str | None,
        "email_col":       str | None,
        "phone_col":       str | None,
        "extra_cols":      list[str],   # order-preserving, no duplicates
    }
    """
    mapping: dict = {
        "cv_filename_col": None,
        "name_col":        None,
        "email_col":       None,
        "phone_col":       None,
        "extra_cols":      [],
    }
    known: set[str] = set()

    for header in headers:
        if not header or not header.strip():
            continue
        norm = header.lower().strip()
        if norm in _CV_FILENAME_ALIASES and mapping["cv_filename_col"] is None:
            mapping["cv_filename_col"] = header
            known.add(header)
        elif norm in _NAME_ALIASES and mapping["name_col"] is None:
            mapping["name_col"] = header
            known.add(header)
        elif norm in _EMAIL_ALIASES and mapping["email_col"] is None:
            mapping["email_col"] = header
            known.add(header)
        elif norm in _PHONE_ALIASES and mapping["phone_col"] is None:
            mapping["phone_col"] = header
            known.add(header)

    seen: set[str] = set()
    for header in headers:
        if header and header not in known and header not in seen:
            mapping["extra_cols"].append(header)
            seen.add(header)

    return mapping


# ── Filename matching ─────────────────────────────────────────────────────────

def match_cv_files(excel_filename: str | None, zip_files: list[dict]) -> dict:
    """
    Match the CV filename stated in an Excel row to files present in the ZIP.

    Matching sequence:
    1. Exact match on filename
    2. Case-insensitive match on filename
    3. Stem match (filename without extension)

    Returns {
        "status":       "matched" | "not_found" | "multiple_matches" | "empty_filename",
        "matched_file": dict | None,    # the single matched file (if status=="matched")
        "match_type":   "exact" | "case_insensitive" | "no_extension" | None,
        "all_matches":  list[dict],     # all candidates (useful for multiple_matches)
    }
    """
    if not excel_filename or not str(excel_filename).strip():
        return {"status": "empty_filename", "matched_file": None,
                "match_type": None, "all_matches": []}

    filename = str(excel_filename).strip()

    # 1. Exact match
    exact = [f for f in zip_files if f["filename"] == filename]
    if len(exact) == 1:
        return {"status": "matched", "matched_file": exact[0],
                "match_type": "exact", "all_matches": exact}
    if len(exact) > 1:
        return {"status": "multiple_matches", "matched_file": None,
                "match_type": "exact", "all_matches": exact}

    # 2. Case-insensitive match
    ci = [f for f in zip_files if f["normalized_name"] == filename.lower()]
    if len(ci) == 1:
        return {"status": "matched", "matched_file": ci[0],
                "match_type": "case_insensitive", "all_matches": ci}
    if len(ci) > 1:
        return {"status": "multiple_matches", "matched_file": None,
                "match_type": "case_insensitive", "all_matches": ci}

    # 3. Stem match (ignore extension)
    stem = Path(filename).stem.lower()
    stem_matches = [f for f in zip_files if f["normalized_stem"] == stem]
    if len(stem_matches) == 1:
        return {"status": "matched", "matched_file": stem_matches[0],
                "match_type": "no_extension", "all_matches": stem_matches}
    if len(stem_matches) > 1:
        return {"status": "multiple_matches", "matched_file": None,
                "match_type": "no_extension", "all_matches": stem_matches}

    return {"status": "not_found", "matched_file": None,
            "match_type": None, "all_matches": []}


# ── Duplicate detection ───────────────────────────────────────────────────────

def find_duplicate_filenames(rows: list[dict], cv_filename_col: str | None) -> set[str]:
    """
    Return the set of CV filenames that appear more than once in the Excel rows.
    Empty filenames are not included (they're handled as a separate error).
    """
    if not cv_filename_col:
        return set()
    counts: dict[str, int] = {}
    for row in rows:
        fn = _cell_str(row.get(cv_filename_col))
        if fn:
            counts[fn] = counts.get(fn, 0) + 1
    return {fn for fn, count in counts.items() if count > 1}


# ── Validation ────────────────────────────────────────────────────────────────

def validate_row(
    row: dict,
    col_mapping: dict,
    match_result: dict,
    duplicate_filenames: set[str],
) -> tuple[list[str], list[str]]:
    """
    Validate a single Excel row and return (errors, warnings).

    Errors are critical and block import.
    Warnings allow import but flag issues for recruiter review.

    col_mapping: output of map_columns()
    match_result: output of match_cv_files()
    duplicate_filenames: set of filenames that appear more than once in the sheet
    """
    errors:   list[str] = []
    warnings: list[str] = []

    cv_col   = col_mapping.get("cv_filename_col")
    filename = _cell_str(row.get(cv_col) if cv_col else None)

    # ── Critical errors ──────────────────────────────────────────────────────
    if not filename:
        errors.append("CV file name is empty")
    elif filename in duplicate_filenames:
        errors.append(f"Duplicate CV filename in spreadsheet: '{filename}'")
    else:
        ms = match_result.get("status")
        if ms == "not_found":
            errors.append(f"CV file not found in ZIP: '{filename}'")
        elif ms == "multiple_matches":
            errors.append(f"Multiple CV files match '{filename}' in ZIP")
        elif ms == "matched":
            mf = match_result.get("matched_file") or {}
            if not mf.get("is_supported", True):
                ext = mf.get("extension", "unknown")
                errors.append(f"Unsupported CV file type: '{ext}'")

    # ── Warnings ─────────────────────────────────────────────────────────────
    name_col = col_mapping.get("name_col")
    if name_col:
        if not _cell_str(row.get(name_col)):
            warnings.append("Missing candidate name")
    else:
        warnings.append("Missing candidate name")

    email_col = col_mapping.get("email_col")
    if email_col:
        if not _cell_str(row.get(email_col)):
            warnings.append("Missing candidate email")
    else:
        warnings.append("Missing candidate email")

    phone_col = col_mapping.get("phone_col")
    if phone_col:
        if not _cell_str(row.get(phone_col)):
            warnings.append("Missing candidate phone")
    else:
        warnings.append("Missing candidate phone")

    # Warn on empty extra columns (potential KO answers / public form fields)
    for col in col_mapping.get("extra_cols", []):
        if not _cell_str(row.get(col)):
            warnings.append(f"Empty answer for column: '{col}'")

    return errors, warnings


# ── Candidate data builder ────────────────────────────────────────────────────

def build_candidate_data(row: dict, col_mapping: dict) -> dict:
    """
    Build the candidate_data JSONB dict from a parsed row.
    Only known optional fields (name, email, phone) are mapped here.
    Extra columns go to build_knockout_answers() instead.
    """
    data: dict = {}

    name_col = col_mapping.get("name_col")
    if name_col:
        val = _cell_str(row.get(name_col))
        if val:
            data["name"] = val

    email_col = col_mapping.get("email_col")
    if email_col:
        val = _cell_str(row.get(email_col))
        if val:
            data["email"] = val

    phone_col = col_mapping.get("phone_col")
    if phone_col:
        val = _cell_str(row.get(phone_col))
        if val:
            data["phone"] = val

    return data


# ── Knockout answer builder ───────────────────────────────────────────────────

def build_knockout_answers(row: dict, col_mapping: dict) -> dict:
    """
    Build the knockout_answers JSONB dict from extra (unmapped) columns.
    Keys are raw column header text; values are raw cell values as strings.
    Stored with original headers before question-ID matching (Phase 3).
    """
    answers: dict = {}
    for col in col_mapping.get("extra_cols", []):
        answers[col] = _cell_str(row.get(col))
    return answers


# ── ZIP summary ───────────────────────────────────────────────────────────────

def compute_zip_summary(zip_files: list[dict], referenced_filenames: set[str]) -> dict:
    """
    Compute a ZIP-level summary after matching all Excel rows.

    referenced_filenames: set of filenames actually matched to an Excel row
                          (use the matched_file["filename"], not the Excel text)

    Returns {
        total_files, supported_files, unsupported_files,
        unmatched_cv_count, unmatched_cv_filenames
    }
    """
    total       = len(zip_files)
    supported   = sum(1 for f in zip_files if f["is_supported"])
    unsupported = total - supported

    ref_norm = {fn.lower() for fn in referenced_filenames}
    unmatched = [
        f["filename"] for f in zip_files
        if f["normalized_name"] not in ref_norm
    ]
    return {
        "total_files":             total,
        "supported_files":         supported,
        "unsupported_files":       unsupported,
        "unmatched_cv_count":      len(unmatched),
        "unmatched_cv_filenames":  unmatched,
    }
