"""The version-1 reference-set manifest: counts, the 70 unreviewed rows, and hashes that match the files as they are now."""
import csv
import hashlib
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = json.loads((HERE / "reference_set_v1_manifest.json").read_text(encoding="utf-8"))
SHEET = list(csv.DictReader((HERE / "reviewer_sheet.csv").open(encoding="utf-8")))
QUEUE = list(csv.DictReader((HERE / "review_queue.csv").open(encoding="utf-8")))
FROZEN_SCORER = HERE.parent.parent / "scripts" / "requirements_v2_extraction_eval.py"


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_the_counts_are_44_reviewed_parent_rows_8_child_units_and_70_unreviewed():
    c = MANIFEST["counts"]
    assert c["candidate_sheet_rows"] == 114
    assert c["reviewed_parent_rows"] == 44 and len(QUEUE) == 44
    assert c["reviewed_parent_rows_by_status"] == {"resolved": 42, "resolved_by_children": 2}
    assert c["decided_child_units"] == 8
    assert c["unreviewed_candidate_rows"] == 70 and c["parent_rows_unresolved"] == 0


def test_the_unreviewed_rows_are_exactly_the_sheet_rows_that_are_not_in_the_queue():
    queued = {q["unit_id"] for q in QUEUE}
    expected = sorted(r["unit_id"] for r in SHEET if r["unit_id"] not in queued)
    listed = sorted(u["unit_id"] for u in MANIFEST["unreviewed_candidate_rows"])
    assert listed == expected and len(listed) == 70
    assert not (set(listed) & queued), "no reviewed row is listed as unreviewed"


def test_the_scope_statement_refuses_to_describe_the_set_as_114_validated_rows():
    assert "does not" in MANIFEST["scope_statement"].lower() or "not validated" in MANIFEST["scope_statement"]
    md = (HERE / "REFERENCE_SET_V1.md").read_text(encoding="utf-8")
    assert "**unreviewed candidate rows (NOT validated)** | **70**" in md
    assert "does **not** validate the whole 114-row" in md


def test_every_recorded_submission_and_amendment_hash_matches_the_file_as_it_is_now():
    for entry in MANIFEST["submissions"] + MANIFEST["child_submissions"] + MANIFEST["amendments"]:
        assert sha(HERE / "reviews" / entry["file"]) == entry["sha256"], entry["file"]


def test_the_input_hashes_match_the_files_and_the_frozen_scorer_is_unchanged():
    assert sha(HERE / "reviewer_sheet.csv") == MANIFEST["inputs"]["reviewer_sheet_sha256"]
    assert sha(HERE / "review_children.csv") == MANIFEST["inputs"]["review_children_sha256"]
    for name, expected in MANIFEST["inputs"]["annotations"].items():
        assert sha(HERE / "annotations" / name) == expected, name
    assert sha(FROZEN_SCORER) == MANIFEST["frozen_benchmark_scorer_sha256"]
    assert sha(FROZEN_SCORER).startswith("c4adf92b9844943e"), "the frozen benchmark scorer must not change"


def test_the_parent_rows_are_listed_with_their_status_and_none_is_open():
    assert set(MANIFEST["parent_rows"].values()) <= {"resolved", "resolved_by_children"}
    assert len(MANIFEST["parent_rows"]) == 44
