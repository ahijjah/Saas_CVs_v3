"""Offline tests of the review queue: priorities, batch size, scope, determinism, and that nothing reviewed was changed."""
import csv
import json
import pathlib

import pytest

import build_queue as Q

HERE = pathlib.Path(__file__).resolve().parents[1]
SHEET_ROWS = list(csv.DictReader((HERE / "reviewer_sheet.csv").open(encoding="utf-8")))
QUEUE = list(csv.DictReader((HERE / "review_queue.csv").open(encoding="utf-8")))
SUBMISSION = list(csv.DictReader((HERE / "reviews" / "CONFIRMED_queue_Asaad-Hijjah_2026-10-15.csv").open(encoding="utf-8")))
SUBMISSION_BY_RQ = {r["rq_id"]: r for r in SUBMISSION}
QUEUE_BY_RQ = {r["rq_id"]: r for r in QUEUE}
MANIFEST = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))


def rebuilt():
    return Q.assign(SHEET_ROWS)


# ── nothing reviewed was changed ──────────────────────────────────────────────────────────────────────────────────────────
def test_every_candidate_annotation_still_matches_the_manifest():
    for jd_id, meta in MANIFEST["jds"].items():
        assert Q.sha_file(HERE / "annotations" / f"{jd_id}.candidate.json") == meta["annotation_sha256"]


def test_the_only_reviewer_decisions_are_the_six_whole_unit_matches_from_the_package():
    filled = [r for r in SHEET_ROWS if r["reviewer_decision"]]
    assert len(filled) == 6
    assert all("whole unit" in r["existing_confirmed_judgment"] for r in filled)


def test_the_queue_shows_a_decision_only_for_the_rows_in_the_confirmed_submission():
    submitted = {r["rq_id"]: r["confirmed_decision"] for r in SUBMISSION}
    for r in QUEUE:
        if r["rq_id"] in submitted:
            assert r["reviewer_decision"] == submitted[r["rq_id"]]
        else:
            assert r["reviewer_decision"] == "" and r["review_status"] == "open"


def test_the_queue_only_reads_the_sheet_and_candidates(tmp_path):
    before = {p: Q.sha_file(p) for p in [HERE / "reviewer_sheet.csv", HERE / "manifest.json"]}
    Q.assign(SHEET_ROWS)
    assert {p: Q.sha_file(p) for p in before} == before


# ── batches and membership ─────────────────────────────────────────────────────────────────────────────────────────────
def test_no_batch_holds_more_than_five_rows_and_every_row_is_in_exactly_one_batch():
    counts = {}
    for r in QUEUE:
        counts[r["batch"]] = counts.get(r["batch"], 0) + 1
    assert max(counts.values()) <= Q.BATCH_SIZE
    assert len({r["rq_id"] for r in QUEUE}) == len(QUEUE)
    assert len({r["unit_id"] for r in QUEUE}) == len(QUEUE), "a unit is queued once, under its highest priority"


def test_every_queued_row_exists_in_the_sheet_with_the_same_exact_text():
    text = {r["unit_id"]: r["source_text"] for r in SHEET_ROWS}
    assert all(text[r["unit_id"]] == r["source_text"] for r in QUEUE)


def test_every_background_and_uncertain_unit_is_priority_one():
    p1 = {r["unit_id"] for r in QUEUE if r["priority"] == "1"}
    expected = {r["unit_id"] for r in SHEET_ROWS if r["proposed_type_candidate"] in ("background", "uncertain")}
    assert expected <= p1 and len(expected) == 9


def test_every_heading_with_requirement_language_is_priority_one():
    p1 = {r["unit_id"] for r in QUEUE if r["priority"] == "1"}
    hits = [r for r in SHEET_ROWS if r["proposed_type_candidate"] == "heading"
            and any(t in r["source_text"].lower() for t in Q.HEADING_TERMS)]
    assert hits and all(r["unit_id"] in p1 for r in hits)


def test_plain_headings_without_requirement_language_are_not_in_priority_one():
    plain = [r for r in SHEET_ROWS if r["proposed_type_candidate"] == "heading"
             and not any(t in r["source_text"].lower() for t in Q.HEADING_TERMS)]
    p1 = {r["unit_id"] for r in QUEUE if r["priority"] == "1"}
    assert plain and all(r["unit_id"] not in p1 for r in plain)


def test_every_s_linked_row_is_queued_and_its_scope_is_kept():
    linked = {r["unit_id"]: r for r in SHEET_ROWS if r["existing_confirmed_judgment"]}
    queued = {r["unit_id"]: r for r in QUEUE}
    assert set(linked) <= set(queued)
    for uid, r in linked.items():
        assert queued[uid]["judgment_scope"] == Q.judgment_scope(r["existing_confirmed_judgment"])
    assert {queued[uid]["judgment_scope"] for uid in linked} == {"whole unit", "sentence within unit"}


def test_a_sentence_level_judgment_is_marked_as_sentence_level_in_the_queue():
    s010 = [r for r in QUEUE if "S010" in r["existing_confirmed_judgment"]]
    assert s010 and all(r["judgment_scope"] == "sentence within unit" for r in s010)
    s002 = [r for r in QUEUE if "S002" in r["existing_confirmed_judgment"]]
    assert s002 and all(r["judgment_scope"] == "whole unit" for r in s002)


def test_priority_three_is_two_requirement_rows_per_jd_chosen_deterministically_and_outside_priorities_one_and_two():
    p3 = [r for r in QUEUE if r["priority"] == "3"]
    per_jd = {}
    for r in p3:
        per_jd[r["jd_id"]] = per_jd.get(r["jd_id"], 0) + 1
        assert r["proposed_type_candidate"] == "requirement"
    assert per_jd == {j: 2 for j in Q.JD_ORDER}
    again = rebuilt()
    assert [r["unit_id"] for r in again if r["priority"] == 3] == [r["unit_id"] for r in QUEUE if r["priority"] == "3"]


def test_the_first_five_tc20_high_risk_rows_are_the_objective_paragraphs_then_requirement_headings():
    tc20 = [r for r in QUEUE if r["jd_id"] == "TC20" and r["priority"] == "1"][:5]
    assert [r["proposed_type_candidate"] for r in tc20] == ["background", "background", "background", "heading", "heading"]
    assert [int(r["first_line"]) for r in tc20][:3] == [3, 5, 9]


def test_the_queue_offers_exactly_the_four_choices():
    assert all(r["choices"] == "requirement | heading | background | uncertain" for r in QUEUE)


def test_the_md_summary_matches_the_csv_counts():
    md = (HERE / "REVIEW_QUEUE.md").read_text(encoding="utf-8")
    assert f"Rows queued: **{len(QUEUE)}** of {len(SHEET_ROWS)}" in md
    summary = json.loads((HERE / "results" / "review_queue_summary.json").read_text(encoding="utf-8"))
    assert summary["queued_rows"] == len(QUEUE) and summary["batches"] == len({r["batch"] for r in QUEUE})
    for p in ("1", "2", "3"):
        assert summary["by_priority"][p] == sum(1 for r in QUEUE if r["priority"] == p)


# ── confirmed reviewer submission (2026-10-15) ──────────────────────────────────────────────────────────────────────────
CANDIDATES = {jd: json.loads((HERE / "annotations" / f"{jd}.candidate.json").read_text(encoding="utf-8")) for jd in MANIFEST["jds"]}


def test_the_submission_holds_exactly_the_five_decisions_with_reviewer_and_date():
    assert [r["rq_id"] for r in SUBMISSION] == ["RQ-001", "RQ-002", "RQ-003", "RQ-004", "RQ-005"]
    assert all(r["reviewer"] == "Asaad Hijjah" and r["confirmed_date"] == "2026-10-15" for r in SUBMISSION)


def test_the_decisions_are_the_ones_given():
    got = {r["rq_id"]: r["confirmed_decision"] for r in SUBMISSION}
    assert got == {"RQ-001": "requirement", "RQ-002": "requirement", "RQ-003": "uncertain", "RQ-004": "heading", "RQ-005": "heading"}


def test_each_submitted_unit_matches_the_sheet_exactly_and_keeps_the_candidate_type():
    sheet = {r["unit_id"]: r for r in SHEET_ROWS}
    for r in SUBMISSION:
        assert sheet[r["unit_id"]]["source_text"] == r["source_text"]
        assert r["candidate_type_preserved"] == sheet[r["unit_id"]]["proposed_type_candidate"]


def test_the_candidate_classifications_are_preserved_in_their_files():
    tc20 = {l["first_line"]: l["proposed_type"] for l in CANDIDATES["TC20"]["lines"]}
    assert tc20[3] == "background" and tc20[5] == "background" and tc20[9] == "background"
    assert tc20[7] == "heading" and tc20[23] == "heading"


def test_the_reason_is_recorded_as_given_and_missing_reasons_are_stated_not_invented():
    assert SUBMISSION_BY_RQ["RQ-003"]["reason"] == \
        "substantive duty summary, but potentially duplicates the detailed duties that follow"
    for rq in ("RQ-001", "RQ-002", "RQ-004", "RQ-005"):
        assert SUBMISSION_BY_RQ[rq]["reason"] == "no reason recorded (reviewer gave none)"


def test_rq_003_is_unresolved_and_never_a_confirmed_requirement_or_exclusion():
    r = SUBMISSION_BY_RQ["RQ-003"]
    assert r["status"].startswith("unresolved") and "must not be used" in r["benchmark_use"]
    assert QUEUE_BY_RQ["RQ-003"]["review_status"].startswith("unresolved")


def test_four_rows_are_resolved_and_one_remains_unresolved_in_the_queue_and_summary():
    resolved = [r for r in QUEUE if r["review_status"] == "resolved"]
    assert {r["rq_id"] for r in resolved} == {"RQ-001", "RQ-002", "RQ-004", "RQ-005"}
    summary = json.loads((HERE / "results" / "review_queue_summary.json").read_text(encoding="utf-8"))
    assert summary["review_status"]["resolved"] == 4 and summary["review_status"]["unresolved (uncertain)"] == 1


def test_a_submission_that_does_not_match_the_sheet_is_refused(tmp_path, monkeypatch):
    bad = tmp_path / "reviews"
    bad.mkdir()
    (bad / "CONFIRMED_bad.csv").write_text(
        "rq_id,unit_id,source_text,confirmed_decision,reviewer,confirmed_date,status,benchmark_use,reason\n"
        "RQ-001,c0c7132c6bd1:L3:29-511,A different sentence,requirement,X,2026-10-15,resolved,,\n", encoding="utf-8")
    monkeypatch.setattr(Q, "HERE", tmp_path)
    with pytest.raises(SystemExit, match="does not match"):
        Q.load_submissions()


def test_an_invalid_decision_is_refused_by_the_recorder(monkeypatch):
    import record_confirmed_decisions as R2
    monkeypatch.setattr(R2, "DECISIONS", [("RQ-001", "maybe", None)])
    with pytest.raises(SystemExit, match="not one of the four choices"):
        R2.build_rows()


def test_the_replay_counts_rq_001_and_rq_002_as_requirements_and_rq_003_only_as_unresolved():
    import replay_ref as RR
    replay = json.loads((HERE / "results" / "replay_ref.json").read_text(encoding="utf-8"))
    tc20_groups = [g for g in replay["groups"] if g.startswith("TC20")]
    for g in tc20_groups:
        for a in replay["groups"][g]["TC20"]:
            assert a["coverage"]["requirement_units"] == 37, "35 candidate requirements plus the two confirmed requirement paragraphs"
            assert not any(s["first_line"] == 9 for s in a["uncovered"]["requirement"]), "RQ-003 is never a requirement span"
            assert not any(s["first_line"] == 9 for s in a["uncovered"].get("requirement", []))
    unresolved_lines = {s["first_line"] for g in tc20_groups for a in replay["groups"][g]["TC20"] for s in a["uncovered"].get("unresolved", [])}
    assert unresolved_lines <= {9}


def test_the_effective_type_of_rq_003_is_unresolved_and_its_candidate_stays_background():
    import replay_ref as RR
    types = RR.effective_types("TC20", CANDIDATES["TC20"])
    assert types[9] == "unresolved" and types[3] == "requirement" and types[5] == "requirement" and types[7] == "heading"
    assert {l["first_line"]: l["proposed_type"] for l in CANDIDATES["TC20"]["lines"]}[9] == "background"
