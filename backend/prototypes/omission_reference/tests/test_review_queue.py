"""Offline tests of the review queue: priorities, batch size, scope, determinism, and that nothing reviewed was changed."""
import csv
import json
import pathlib

import build_queue as Q

HERE = pathlib.Path(__file__).resolve().parents[1]
SHEET_ROWS = list(csv.DictReader((HERE / "reviewer_sheet.csv").open(encoding="utf-8")))
QUEUE = list(csv.DictReader((HERE / "review_queue.csv").open(encoding="utf-8")))
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


def test_the_queue_leaves_every_reviewer_decision_blank():
    assert all(r["reviewer_decision"] == "" for r in QUEUE)


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
