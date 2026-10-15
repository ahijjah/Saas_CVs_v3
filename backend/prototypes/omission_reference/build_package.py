"""Builds the multilingual omission-reference package from EXISTING fixtures. Offline: reads files, writes files, calls nothing.

Outputs (all under this folder):
  jd/<id>.txt                       the exact JD text as stored in its fixture (UTF-8 bytes, no normalisation)
  annotations/<id>.candidate.json   CLAUDE-GENERATED candidate classification of every non-blank line. NOT human-confirmed.
  reviewer_sheet.csv                one row per source unit across all JDs; reviewer_decision is filled only where an existing confirmed
                                    judgment's statement equals the whole unit exactly
  manifest.json                     source files, hashes, unit counts, and the hash of this build script
"""
from __future__ import annotations

import csv
import hashlib
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
BACKEND = HERE.parent.parent
import sys  # noqa: E402

sys.path[:0] = [str(HERE.parent / "omission_inventory"), str(BACKEND)]
import inventory as I  # noqa: E402

CONSOLIDATED = BACKEND / "tests" / "fixtures" / "requirements_v2_review_package_v2" / "reviews" / "CONSOLIDATED_judgments_S001-S012.csv"
BENCH = BACKEND / "tests" / "fixtures" / "requirements_v2_benchmark" / "cases"
TC20 = BACKEND / "tests" / "fixtures" / "requirements_v2_technical_coordinator_20" / "evidence.json"
JDS = [
    {"id": "TC20", "language": "en", "source": TC20, "field": "description", "roles": "duties (16), qualifications, OR (experience and familiarity), independent AND (competencies), location, reporting"},
    {"id": "B02_en_data_analyst", "language": "en", "source": BENCH / "B02_en_data_analyst.json", "field": "jd", "roles": "OR (Python or R), independent AND (SQL, Power BI), duties"},
    {"id": "B03_en_warehouse_supervisor", "language": "en", "source": BENCH / "B03_en_warehouse_supervisor.json", "field": "jd", "roles": "independent AND (languages), conditions (shifts, travel, visa or relocation), location, duties, preferred"},
    {"id": "B07_ar_accountant", "language": "ar", "source": BENCH / "B07_ar_accountant.json", "field": "jd", "roles": "company background, conditions (salary, location), benefits, duties, preferred"},
    {"id": "B08_ar_hr_specialist", "language": "ar", "source": BENCH / "B08_ar_hr_specialist.json", "field": "jd", "roles": "OR (Arabic or English), independent AND (labour law and social insurance), duties, repeated note"},
    {"id": "B12_ar_injection", "language": "ar", "source": BENCH / "B12_ar_injection.json", "field": "jd", "roles": "independent AND (CSS and HTML), genuine conflict, AI-directed injection text (data, not requirement)"},
]

# Candidate classification by physical line number (1-based). Every other non-blank line is a requirement candidate with the section reason.
# The types are Claude-generated proposals for human review. They are not confirmed by anyone.
OVERRIDES = {
    "B02_en_data_analyst": {1: ("heading", "title line of the posting"), 3: ("heading", "section heading 'Requirements'"),
                            11: ("heading", "section heading 'What you will do'"),
                            16: ("uncertain", "general expectation sentence with no section; may or may not be a requirement")},
    "B03_en_warehouse_supervisor": {1: ("heading", "title line of the posting"), 3: ("uncertain", "introduction; names the work location (Jebel Ali) which would be a non-scoreable location condition"),
                                    5: ("heading", "section heading 'Required:'"), 11: ("heading", "section heading 'Preferred:'"),
                                    15: ("heading", "section heading 'Duties:'")},
    "B07_ar_accountant": {1: ("heading", "title line of the posting"), 3: ("background", "company description ('نبذة عن الشركة'), optional background"),
                          5: ("heading", "section heading 'المهام:'"), 10: ("heading", "section heading 'المتطلبات:'"),
                          17: ("heading", "section heading 'يفضل:'")},
    "B08_ar_hr_specialist": {1: ("heading", "title line of the posting"), 3: ("heading", "section heading 'المتطلبات:'"),
                             11: ("heading", "section heading 'الأعمال اليومية:'"),
                             15: ("uncertain", "closing note repeating the communication requirement of line 9; may be a repetition, not a new requirement")},
    "B12_ar_injection": {1: ("heading", "title line of the posting"), 3: ("heading", "section heading 'المتطلبات:'"),
                         9: ("heading", "section heading 'يفضل:'"), 12: ("uncertain", "recruiter note that contradicts line 6 (genuine conflict; needs review, not decided here)"),
                         14: ("uncertain", "text addressed to an AI system: data, not a requirement; must not be extracted"),
                         16: ("heading", "section heading 'المهام:'")},
}


def tc20_overrides(jd: str) -> dict:
    """The TC20 headings and background lines from its reviewed-candidate annotation file (exact text, same as the earlier replay)."""
    ann = json.loads((HERE.parent / "omission_inventory" / "annotations" / "tc20.json").read_text(encoding="utf-8"))
    lines = jd.split("\n")
    out = {}
    for n, line in enumerate(lines, 1):
        s = line.strip()
        if s in ann["headings"]:
            out[n] = ("heading", "section heading in the posting")
        elif s in ann["background"]:
            out[n] = ("background", "objective or lead-in paragraph (candidate background)")
    return out


def load_jd(entry: dict) -> tuple[str, bytes]:
    path = entry["source"]
    raw_bytes = path.read_bytes()
    if path.suffix == ".json":
        text = json.loads(raw_bytes.decode("utf-8"))[entry["field"]]
    else:
        text = json.loads(raw_bytes.decode("utf-8"))[entry["field"]]
    return text, raw_bytes


def section_reasons(lines: list[str], overrides: dict) -> dict:
    """Reason for each non-blank line: the candidate override, or the requirement's section (the last heading before it)."""
    section, out = None, {}
    for n, line in enumerate(lines, 1):
        s = line.strip()
        if not s:
            continue
        if n in overrides:
            kind, reason = overrides[n]
            if kind == "heading":
                section = s.rstrip(":").strip()
            out[n] = (kind, reason)
        else:
            out[n] = ("requirement", f"requirement line under '{section}'" if section else "requirement line (no section heading)")
    return out


def confirmed_matches(unit_text: str) -> list[tuple[dict, str]]:
    """Confirmed judgments whose statement is in this unit. Scope 'whole unit': the statement equals the unit's text after a leading bullet
    is removed. Scope 'sentence within unit': the statement is an exact substring of a longer unit. Only 'whole unit' may carry a reviewer
    decision; a sentence-level judgment is recorded but leaves the decision blank."""
    bare = unit_text.strip()
    for mark in ("- ", "• "):
        if bare.startswith(mark):
            bare = bare[len(mark):].strip()
    hits = []
    with CONSOLIDATED.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            statement = row["jd_statement_exact"].strip()
            if statement == bare:
                hits.append((row, "whole unit"))
            elif statement and statement in bare:
                hits.append((row, "sentence within unit"))
    return hits


def main() -> int:
    (HERE / "jd").mkdir(exist_ok=True)
    (HERE / "annotations").mkdir(exist_ok=True)
    manifest = {"build_script_sha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(), "jds": {}}
    sheet_rows = []
    for entry in JDS:
        jd_id = entry["id"]
        text, source_bytes = load_jd(entry)
        (HERE / "jd" / f"{jd_id}.txt").write_bytes(text.encode("utf-8"))
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        overrides = tc20_overrides(text) if jd_id == "TC20" else OVERRIDES[jd_id]
        lines = text.split("\n")
        reasons = section_reasons(lines, overrides)
        headings = [lines[n - 1].strip() for n, (k, _) in reasons.items() if k == "heading"]
        background = [lines[n - 1].strip() for n, (k, _) in reasons.items() if k == "background"]
        candidate = {"jd_id": jd_id, "status": "CLAUDE-GENERATED CANDIDATE - NOT HUMAN-CONFIRMED", "jd_sha256": sha,
                     "source_file": str(entry["source"].relative_to(BACKEND)), "source_file_sha256": hashlib.sha256(source_bytes).hexdigest(),
                     "lines": [{"first_line": n, "text": lines[n - 1], "proposed_type": k, "reason": r} for n, (k, r) in reasons.items()]}
        (HERE / "annotations" / f"{jd_id}.candidate.json").write_text(json.dumps(candidate, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        inv = I.build_inventory(text, {"jd_sha256": sha, "headings": headings, "background": background})
        by_line = {n: reasons[n] for n in reasons}
        for u in inv.units:
            kind, reason = by_line[u.first_line]
            matches = confirmed_matches(u.text)
            judgment = "; ".join(f"{m['review_id']} ({m['reviewer_choice']}, {scope}, statement: {m['jd_statement_exact'][:60]})" for m, scope in matches)
            decision = "; ".join(f"{m['reviewer_choice']} [{m['review_id']}, confirmed 2026-10-12 by Asaad Hijjah]"
                                 for m, scope in matches if scope == "whole unit")
            sheet_rows.append({"jd_id": jd_id, "language": entry["language"], "unit_id": u.unit_id, "first_line": u.first_line,
                               "start": u.spans[0][0], "end": u.spans[-1][1], "source_text": u.text,
                               "proposed_type_candidate": kind, "candidate_reason": reason,
                               "existing_confirmed_judgment": judgment, "reviewer_decision": decision})
        manifest["jds"][jd_id] = {"language": entry["language"], "source_file": candidate["source_file"],
                                  "source_file_sha256": candidate["source_file_sha256"], "jd_sha256": sha, "jd_length": len(text),
                                  "physical_lines": len(lines), "units": len(inv.units), "requirement_units": len(inv.requirements()),
                                  "candidate_counts": {k: sum(1 for v in reasons.values() if v[0] == k) for k in ("requirement", "heading", "background", "uncertain")},
                                  "roles": entry["roles"], "annotation_sha256": hashlib.sha256((HERE / "annotations" / f"{jd_id}.candidate.json").read_bytes()).hexdigest()}
    with (HERE / "reviewer_sheet.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(sheet_rows[0].keys()))
        writer.writeheader()
        writer.writerows(sheet_rows)
    manifest["reviewer_sheet_rows"] = len(sheet_rows)
    (HERE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {len(JDS)} JDs, {len(sheet_rows)} reviewer rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
