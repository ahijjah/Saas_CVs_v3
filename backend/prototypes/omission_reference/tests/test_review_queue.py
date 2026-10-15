"""Offline tests of the review queue: priorities, batch size, scope, determinism, and that nothing reviewed was changed."""
import csv
import json
import pathlib

import pytest

import build_queue as Q

HERE = pathlib.Path(__file__).resolve().parents[1]
SHEET_ROWS = list(csv.DictReader((HERE / "reviewer_sheet.csv").open(encoding="utf-8")))
QUEUE = list(csv.DictReader((HERE / "review_queue.csv").open(encoding="utf-8")))
SUBMISSION = [r for path in sorted((HERE / "reviews").glob("CONFIRMED_*.csv")) for r in csv.DictReader(path.open(encoding="utf-8"))]
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
        if r["rq_id"] in ("RQ-029", "RQ-030"):
            assert r["reviewer_decision"] == "resolved_by_children", "the parent's uncertain submission is preserved; its children decide it"
        elif r["rq_id"] in submitted:
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


def test_the_submissions_hold_exactly_the_ten_decisions_with_reviewer_and_date():
    assert sorted(r["rq_id"] for r in SUBMISSION) == [f"RQ-{n:03d}" for n in range(1, 31)]
    assert all(r["reviewer"] == "Asaad Hijjah" and r["confirmed_date"] == "2026-10-15" for r in SUBMISSION)


def test_the_decisions_are_the_ones_given():
    got = {r["rq_id"]: r["confirmed_decision"] for r in SUBMISSION}
    assert got == {"RQ-001": "requirement", "RQ-002": "requirement", "RQ-003": "uncertain", "RQ-004": "heading", "RQ-005": "heading",
                   "RQ-006": "heading", "RQ-007": "heading", "RQ-008": "heading", "RQ-009": "heading", "RQ-010": "requirement",
                   "RQ-011": "heading", "RQ-012": "requirement", "RQ-013": "heading", "RQ-014": "heading", "RQ-015": "background",
                   "RQ-016": "heading", "RQ-017": "heading", "RQ-018": "heading", "RQ-019": "requirement", "RQ-020": "heading",
                   "RQ-021": "requirement", "RQ-022": "background", "RQ-023": "heading", "RQ-024": "heading", "RQ-025": "heading",
                   "RQ-026": "requirement", "RQ-027": "requirement", "RQ-028": "requirement", "RQ-029": "uncertain", "RQ-030": "uncertain"}


def test_each_submitted_unit_matches_the_sheet_exactly_and_keeps_the_candidate_type():
    sheet = {r["unit_id"]: r for r in SHEET_ROWS}
    for r in SUBMISSION:
        assert sheet[r["unit_id"]]["source_text"] == r["source_text"]
        assert r["candidate_type_preserved"] == sheet[r["unit_id"]]["proposed_type_candidate"]


def test_the_candidate_classifications_are_preserved_in_their_files():
    tc20 = {l["first_line"]: l["proposed_type"] for l in CANDIDATES["TC20"]["lines"]}
    assert tc20[3] == "background" and tc20[5] == "background" and tc20[9] == "background"
    assert tc20[7] == "heading" and tc20[23] == "heading"


def test_the_reasons_are_recorded_as_given_and_missing_reasons_are_stated_not_invented():
    assert SUBMISSION_BY_RQ["RQ-003"]["reason"] == \
        "substantive duty summary, but potentially duplicates the detailed duties that follow"
    assert SUBMISSION_BY_RQ["RQ-010"]["reason"] == "it explicitly requires clear communication of findings to non-technical colleagues."
    assert SUBMISSION_BY_RQ["RQ-008"]["reason"] == "names the qualifications and professional-experience section and contains no requirement itself."
    for rq in ("RQ-001", "RQ-002", "RQ-004", "RQ-005"):
        assert SUBMISSION_BY_RQ[rq]["reason"] == "no reason recorded (reviewer gave none)"


def test_batch_three_source_text_is_exact_and_rq_012_candidate_is_preserved():
    sheet = {r["unit_id"]: r for r in SHEET_ROWS}
    for rq in ("RQ-011", "RQ-012", "RQ-013", "RQ-014", "RQ-015"):
        r = SUBMISSION_BY_RQ[rq]
        assert sheet[r["unit_id"]]["source_text"] == r["source_text"]
        assert r["candidate_type_preserved"] == sheet[r["unit_id"]]["proposed_type_candidate"]
    assert SUBMISSION_BY_RQ["RQ-012"]["candidate_type_preserved"] == "uncertain"
    assert SUBMISSION_BY_RQ["RQ-015"]["candidate_type_preserved"] == "background"


def test_the_batch_two_source_text_is_exact_and_the_candidates_are_preserved():
    sheet = {r["unit_id"]: r for r in SHEET_ROWS}
    for rq in ("RQ-006", "RQ-007", "RQ-008", "RQ-009", "RQ-010"):
        r = SUBMISSION_BY_RQ[rq]
        assert sheet[r["unit_id"]]["source_text"] == r["source_text"]
        assert r["candidate_type_preserved"] == sheet[r["unit_id"]]["proposed_type_candidate"]
    assert SUBMISSION_BY_RQ["RQ-010"]["candidate_type_preserved"] == "uncertain"


def test_rq_003_is_unresolved_and_never_a_confirmed_requirement_or_exclusion():
    r = SUBMISSION_BY_RQ["RQ-003"]
    assert r["status"].startswith("unresolved") and "must not be used" in r["benchmark_use"]
    assert QUEUE_BY_RQ["RQ-003"]["review_status"].startswith("unresolved")


def test_twenty_seven_rows_are_resolved_and_rq_003_remains_unresolved_in_the_queue_and_summary():
    resolved = {r["rq_id"] for r in QUEUE if r["review_status"] == "resolved"}
    assert resolved == {f"RQ-{n:03d}" for n in range(1, 31)} - {"RQ-003", "RQ-029", "RQ-030"}
    summary = json.loads((HERE / "results" / "review_queue_summary.json").read_text(encoding="utf-8"))
    assert summary["review_status"]["resolved"] == 27 and summary["review_status"]["unresolved (uncertain)"] == 1
    assert summary["review_status"]["resolved_by_children"] == 2


def test_a_submission_that_does_not_match_the_sheet_is_refused(tmp_path, monkeypatch):
    bad = tmp_path / "reviews"
    bad.mkdir()
    (bad / "CONFIRMED_bad.csv").write_text(
        "rq_id,unit_id,source_text,confirmed_decision,reviewer,confirmed_date,status,benchmark_use,reason\n"
        "RQ-001,c0c7132c6bd1:L3:29-511,A different sentence,requirement,X,2026-10-15,resolved,,\n", encoding="utf-8")
    monkeypatch.setattr(Q, "HERE", tmp_path)
    with pytest.raises(SystemExit, match="does not match"):
        Q.load_submissions()


def test_an_invalid_decision_is_refused_by_the_recorder():
    import record_confirmed_decisions as R2
    with pytest.raises(SystemExit, match="not one of the four choices"):
        R2.build_rows([("RQ-001", "maybe", None)], "2026-10-15")


def test_a_submission_is_never_overwritten_by_the_recorder():
    import record_confirmed_decisions as R2
    with pytest.raises(SystemExit, match="never overwritten"):
        R2.main(["--batch", "1"])


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


def test_batch_three_effective_types_and_the_rq_015_background_exclusion():
    import replay_ref as RR
    b03 = json.loads((HERE / "annotations" / "B03_en_warehouse_supervisor.candidate.json").read_text(encoding="utf-8"))
    eff = RR.effective_types("B03_en_warehouse_supervisor", b03)
    assert eff[3] == "requirement" and eff[5] == "heading" and eff[15] == "heading"
    b07 = json.loads((HERE / "annotations" / "B07_ar_accountant.candidate.json").read_text(encoding="utf-8"))
    assert RR.effective_types("B07_ar_accountant", b07)[3] == "background"


def test_the_batch_two_decisions_change_the_effective_types_and_no_candidate_file():
    import replay_ref as RR
    types = RR.effective_types("TC20", CANDIDATES["TC20"])
    assert [types[n] for n in (37, 38, 40, 47)] == ["heading"] * 4
    b02 = json.loads((HERE / "annotations" / "B02_en_data_analyst.candidate.json").read_text(encoding="utf-8"))
    assert RR.effective_types("B02_en_data_analyst", b02)[16] == "requirement"
    cand = {l["first_line"]: l["proposed_type"] for l in b02["lines"]}
    assert cand[16] == "uncertain", "the candidate file still says uncertain"


def test_the_queue_has_no_open_row_at_the_head_of_the_next_batch_that_was_already_decided():
    decided = set(SUBMISSION_BY_RQ)
    assert all(QUEUE_BY_RQ[rq]["review_status"] != "open" for rq in decided)


# ── batch four: relation, repeated content, earlier files preserved, document-order next batch ────────────────────────────
import hashlib  # noqa: E402

RECORDED_HASHES = {  # the earlier submissions and the reviewer sheet, as recorded when they were committed
    "CONFIRMED_queue_Asaad-Hijjah_2026-10-15.csv": "1be5e1906c528a74",
    "CONFIRMED_queue_Asaad-Hijjah_2026-10-15_batch2.csv": "eeb385eb4ae3dd6f",
    "CONFIRMED_queue_Asaad-Hijjah_2026-10-15_batch3.csv": "deee39de2d0b4a1a",
    "CONFIRMED_queue_Asaad-Hijjah_2026-10-15_batch6.csv": "2fcebe9825059dec",
    "CONFIRMED_queue_Asaad-Hijjah_2026-10-15_batch5.csv": "e7d5134305985361",
    "CONFIRMED_queue_Asaad-Hijjah_2026-10-15_batch4.csv": "a967319577e67909",
}


def test_earlier_submissions_are_byte_for_byte_unchanged():
    for name, expected in RECORDED_HASHES.items():
        got = hashlib.sha256((HERE / "reviews" / name).read_bytes()).hexdigest()
        assert got.startswith(expected), name


def test_the_child_units_file_is_unchanged_by_decisions():
    assert hashlib.sha256((HERE / "review_children.csv").read_bytes()).hexdigest().startswith("a01ad44b525ab1a9")


def test_the_reviewer_sheet_is_unchanged_since_the_package_was_built():
    assert hashlib.sha256((HERE / "reviewer_sheet.csv").read_bytes()).hexdigest().startswith("f71cb861d4c072e6")


def test_rq_019_is_the_repeat_relation_and_it_names_exactly_the_b08_line_9_unit():
    related = [r for r in SUBMISSION if r.get("relation", "").startswith("repeats_content_of")]
    assert [r["rq_id"] for r in related] == ["RQ-019"]
    sheet = {r["unit_id"]: r for r in SHEET_ROWS}
    target = sheet["386d19910cf1:L9:234-262"]
    assert target["jd_id"] == "B08_ar_hr_specialist" and target["first_line"] == "9"
    assert target["proposed_type_candidate"] == "requirement"
    assert related[0]["relation"] == "repeats_content_of 386d19910cf1:L9:234-262 (B08_ar_hr_specialist line 9)"


def test_both_units_of_the_rq_019_relation_are_preserved_in_the_submission_and_the_sheet():
    r = SUBMISSION_BY_RQ["RQ-019"]
    assert r["unit_id"] == "386d19910cf1:L15:341-412" and r["candidate_type_preserved"] == "uncertain"
    assert "must not create a second scored criterion" in r["reason"]
    assert "counted only through the related unit" in r["benchmark_use"]


def test_no_other_duplicate_relationship_is_inferred():
    for r in SUBMISSION:
        if r["rq_id"] not in ("RQ-019", "RQ-021"):
            assert r.get("relation", "") == "" and "repeat" not in r["reason"]


def test_the_b08_repeated_unit_is_not_a_second_requirement_in_the_replay():
    import replay_ref as RR
    b08 = json.loads((HERE / "annotations" / "B08_ar_hr_specialist.candidate.json").read_text(encoding="utf-8"))
    eff = RR.effective_types("B08_ar_hr_specialist", b08)
    assert eff[15] == "repeated" and eff[9] == "requirement"
    replay = json.loads((HERE / "results" / "replay_ref.json").read_text(encoding="utf-8"))
    for g, per in replay["groups"].items():
        for a in per.get("B08_ar_hr_specialist", []):
            assert all(sp["first_line"] != 15 for sp in a["uncovered"].get("requirement", []))
            assert a["coverage"]["requirement_units"] == 8, "the repeated unit is not a requirement denominator"


def test_no_child_unit_is_pending_and_the_next_batch_is_five_rows_in_document_order():
    summary = json.loads((HERE / "results" / "review_queue_summary.json").read_text(encoding="utf-8"))
    assert summary["child_units"] == {"total": 8, "decided": 8, "pending_semantic_role": 0}
    assert summary["next_batch"] == ["RQ-033", "RQ-034", "RQ-035", "RQ-031", "RQ-032"]
    assert len(summary["next_batch"]) <= 5
    doc = sorted(summary["next_batch"], key=lambda rq: (Q.JD_ORDER.index(QUEUE_BY_RQ[rq]["jd_id"]), int(QUEUE_BY_RQ[rq]["first_line"])))
    assert summary["next_batch"] == doc, "presented in document order"


def test_priority_one_is_fully_decided_after_batch_five():
    p1 = [r for r in QUEUE if r["priority"] == "1"]
    assert all(r["review_status"] != "open" for r in p1)


# ── batch six: uncertain parents, child review units ──────────────────────────────────────────────────────────────────────
CHILDREN = list(csv.DictReader((HERE / "review_children.csv").open(encoding="utf-8")))
JDS = {jd: (HERE / "jd" / f"{jd}.txt").read_bytes().decode("utf-8") for jd in MANIFEST["jds"]}


def test_batch_six_records_three_requirements_and_two_uncertain_parents():
    got = {r["rq_id"]: (r["confirmed_decision"], r["status"]) for r in SUBMISSION if r["rq_id"] in
           {"RQ-026", "RQ-027", "RQ-028", "RQ-029", "RQ-030"}}
    assert got == {"RQ-026": ("requirement", "resolved"), "RQ-027": ("requirement", "resolved"), "RQ-028": ("requirement", "resolved"),
                   "RQ-029": ("uncertain", "unresolved (uncertain)"), "RQ-030": ("uncertain", "unresolved (uncertain)")}
    assert SUBMISSION_BY_RQ["RQ-027"]["reason"] == "one Python-or-R alternative requirement; S002 confirms the alternatives must remain together."
    assert SUBMISSION_BY_RQ["RQ-029"]["reason"] == \
        "the physical line mixes work conditions, post-hire checks and a benefit. S010 applies only to its first sentence."


def test_uncertain_parents_are_excluded_from_confirmed_inclusion_and_exclusion():
    for rq in ("RQ-029", "RQ-030"):
        assert "excluded from confirmed inclusion and exclusion" in SUBMISSION_BY_RQ[rq]["benchmark_use"]
    assert QUEUE_BY_RQ["RQ-029"]["review_status"] == "resolved_by_children"
    assert QUEUE_BY_RQ["RQ-030"]["review_status"] == "resolved_by_children"
    assert QUEUE_BY_RQ["RQ-003"]["review_status"].startswith("unresolved")


def test_each_parent_is_partitioned_into_children_that_reproduce_every_character():
    for rq, jd_id, parent_unit in (("RQ-029", "B03_en_warehouse_supervisor", "0fc3081a294a:L19:"), ("RQ-030", "B07_ar_accountant", "30fb2f042757:L21:")):
        kids = [c for c in CHILDREN if c["parent_rq_id"] == rq]
        assert kids and all(c["parent_unit_id"].startswith(parent_unit) for c in kids)
        jd = JDS[jd_id]
        start = int(kids[0]["start"])
        end = int(kids[-1]["end"])
        assert jd[start:end] == "".join(jd[int(c["start"]):int(c["end"])] for c in kids), "children reproduce the parent exactly"
        for a, b in zip(kids, kids[1:]):
            assert a["end"] == b["start"], "children are contiguous: no gap, no overlap"
        for c in kids:
            assert c["child_text"] == jd[int(c["content_start"]):int(c["content_end"])].strip()
            assert int(c["start"]) <= int(c["content_start"]) <= int(c["content_end"]) <= int(c["end"])


def test_child_offsets_are_deterministic_and_rebuilt_identically():
    import build_children as BC
    for rq in ("RQ-029", "RQ-030"):
        kids = [c for c in CHILDREN if c["parent_rq_id"] == rq]
        jd_id = kids[0]["jd_id"]
        spans = BC.split_children(JDS[jd_id], int(kids[0]["start"]), int(kids[-1]["end"]))
        assert spans == [(int(c["start"]), int(c["end"])) for c in kids]


def test_an_or_alternative_stays_in_one_child():
    c = next(c for c in CHILDREN if "visa" in c["child_text"])
    assert "a valid UAE residence visa or be willing to relocate" in c["child_text"]
    assert c["child_id"] == "RQ-029/C03"


def test_children_carry_the_six_semantic_role_choices_and_no_decision():
    for c in CHILDREN:
        assert c["semantic_role_choices"] == " | ".join(["scoreable requirement", "non-scoreable condition", "post-hiring condition",
                                                          "informational benefit", "background", "uncertain"])
        assert c["semantic_role_decision"] == ""
        assert c["proposed_role_candidate"] in c["semantic_role_choices"]


def test_sentence_level_judgments_apply_only_to_children_inside_their_statement():
    by = {c["child_id"]: c["existing_judgment_applies"] for c in CHILDREN}
    assert by["RQ-029/C01"].startswith("S010") and by["RQ-029/C02"].startswith("S010") and by["RQ-029/C03"].startswith("S010")
    assert by["RQ-029/C04"] == "" and by["RQ-029/C05"] == ""
    assert "S003" in by["RQ-030/C01"] and "S011" in by["RQ-030/C01"] and "S003" in by["RQ-030/C02"]
    assert by["RQ-030/C03"] == ""


def test_children_do_not_enter_the_replay_until_decided():
    replay = json.loads((HERE / "results" / "replay_ref.json").read_text(encoding="utf-8"))
    expected_blocks = {"requirement", "uncertain", "unresolved", "repeated", "conflict_modifier", "excluded_injection"}
    for g in replay["groups"]:
        for jd, answers in replay["groups"][g].items():
            for a in answers:
                assert set(a["uncovered"]) == expected_blocks, "children add no block: nothing is decided at child level yet"
    b03 = [a for g in replay["groups"] for a in replay["groups"][g].get("B03_en_warehouse_supervisor", [])]
    for a in b03:
        assert a["coverage"]["requirement_units"] == 9, "the uncertain parent is no longer a requirement"
        assert all(sp["first_line"] != 19 for sp in a["uncovered"].get("requirement", []))


def test_rq_ids_are_stable_and_the_queue_is_not_renumbered():
    assert [r["rq_id"] for r in QUEUE][:25] == [f"RQ-{n:03d}" for n in range(1, 26)]


def test_rq_021_is_a_conflict_modifier_of_the_exact_line_six_unit_and_adds_no_requirement():
    r = SUBMISSION_BY_RQ["RQ-021"]
    assert r["relation"] == "modifies_importance_of 945b2154c3f2:L6:101-122 (B12_ar_injection line 6)"
    assert r["confirmed_decision"] == "requirement" and "conflicts with the earlier presentation" in r["reason"]
    assert "never a second scored criterion" in r["benchmark_use"]


def test_rq_022_is_recorded_as_ai_directed_injection_and_excluded():
    r = SUBMISSION_BY_RQ["RQ-022"]
    assert r["classification_tag"] == "ai_directed_injection" and r["confirmed_decision"] == "background"
    assert "excluded from requirement coverage" in r["benchmark_use"] and "injection guard" in r["benchmark_use"]


def test_the_b12_conflict_and_injection_units_are_excluded_from_requirement_counts_and_reported_separately():
    replay = json.loads((HERE / "results" / "replay_ref.json").read_text(encoding="utf-8"))
    b12 = [a for g in replay["groups"] for a in replay["groups"][g].get("B12_ar_injection", [])]
    assert b12
    for a in b12:
        assert a["coverage"]["requirement_units"] == 6, "RQ-021 and RQ-022 add no requirement"
        assert not any(sp["first_line"] in (12, 14) for sp in a["uncovered"].get("requirement", []))
        assert all(sp["first_line"] == 12 for sp in a["uncovered"].get("conflict_modifier", []))
        assert all(sp["first_line"] == 14 for sp in a["uncovered"].get("excluded_injection", []))
        assert isinstance(a["defects"]["injection_guard"], bool), "the injection guard is reported per answer"


def test_the_injection_guard_is_not_applied_to_other_jds_and_is_not_offset_by_coverage():
    replay = json.loads((HERE / "results" / "replay_ref.json").read_text(encoding="utf-8"))
    for g in replay["groups"]:
        for jd, answers in replay["groups"][g].items():
            if jd != "B12_ar_injection":
                assert all(a.get("defects", {}).get("injection_guard") is None for a in answers)


def test_rq_003_is_still_unresolved_after_batch_four():
    assert QUEUE_BY_RQ["RQ-003"]["review_status"].startswith("unresolved")


# ── child decisions for RQ-029 and replay obligations ───────────────────────────────────────────────────────────────────
CHILD_SUB = [r for r in csv.DictReader((HERE / "reviews" / "CHILD_CONFIRMED_queue_Asaad-Hijjah_2026-10-15_RQ-029.csv").open(encoding="utf-8"))]
CHILD_BY_ID = {r["child_id"]: r for r in CHILD_SUB}


def test_the_five_child_decisions_are_recorded_as_given_with_reviewer_and_date():
    assert {r["child_id"]: r["confirmed_role"] for r in CHILD_SUB} == {
        "RQ-029/C01": "non-scoreable condition", "RQ-029/C02": "non-scoreable condition", "RQ-029/C03": "non-scoreable condition",
        "RQ-029/C04": "post-hiring condition", "RQ-029/C05": "informational benefit"}
    assert all(r["reviewer"] == "Asaad Hijjah" and r["confirmed_date"] == "2026-10-15" for r in CHILD_SUB)
    assert CHILD_BY_ID["RQ-029/C03"]["reason"].startswith("valid UAE residence visa or willingness to relocate.")


def test_s010_is_confirmed_for_exactly_c01_to_c03_from_exact_offsets():
    import build_children as BC
    import record_child_decisions as RC
    children = RC.load_children()
    RC.check_s010_scope(children, "RQ-029", ["RQ-029/C01", "RQ-029/C02", "RQ-029/C03"])
    assert all("S010 applies exactly to C01, C02, C03" in r["judgment_scope_confirmed"] for r in CHILD_SUB)
    with pytest.raises(SystemExit):
        RC.check_s010_scope(children, "RQ-029", ["RQ-029/C01", "RQ-029/C02"])


def test_c03_stays_one_condition_and_no_alternatives_or_schema_field_is_introduced():
    c03 = CHILD_BY_ID["RQ-029/C03"]
    assert c03["child_text"] == "and candidates must hold a valid UAE residence visa or be willing to relocate."
    assert "alternatives" not in ",".join(CHILD_SUB[0].keys())
    import replay_ref as RR
    assert set(RR.CHILD_OBLIGATION.values()) == {"non_scoreable_requirements", "post_hiring_conditions", "informational_items"}
    assert [r["child_id"] for r in CHILD_SUB if r["confirmed_role"] == "non-scoreable condition"] == ["RQ-029/C01", "RQ-029/C02", "RQ-029/C03"]


def test_each_child_maps_to_the_list_the_decision_names():
    import replay_ref as RR
    exp = {"non-scoreable condition": "non_scoreable_requirements", "post-hiring condition": "post_hiring_conditions",
           "informational benefit": "informational_items"}
    assert RR.CHILD_OBLIGATION == exp
    replay = json.loads((HERE / "results" / "replay_ref.json").read_text(encoding="utf-8"))
    seen = {}
    for g in replay["groups"]:
        for jd in ("B03_en_warehouse_supervisor", "B07_ar_accountant"):
            for a in replay["groups"][g].get(jd, []):
                for ob in a["child_obligations"]:
                    seen.setdefault(ob["child_id"], set()).add(ob["expected_list"])
                    assert ob["status"] in {"met", "partial", "misrouted", "missing"}
    assert seen == {"RQ-029/C01": {"non_scoreable_requirements"}, "RQ-029/C02": {"non_scoreable_requirements"},
                    "RQ-029/C03": {"non_scoreable_requirements"}, "RQ-029/C04": {"post_hiring_conditions"},
                    "RQ-029/C05": {"informational_items"}, "RQ-030/C01": {"non_scoreable_requirements"},
                    "RQ-030/C02": {"non_scoreable_requirements"}, "RQ-030/C03": {"informational_items"}}


def test_children_are_never_scored_criteria_and_the_parent_is_not_a_requirement_block():
    import replay_ref as RR
    b03 = json.loads((HERE / "annotations" / "B03_en_warehouse_supervisor.candidate.json").read_text(encoding="utf-8"))
    b07 = json.loads((HERE / "annotations" / "B07_ar_accountant.candidate.json").read_text(encoding="utf-8"))
    assert RR.effective_types("B03_en_warehouse_supervisor", b03)[19] == "by_children"
    assert RR.effective_types("B07_ar_accountant", b07)[21] == "by_children"
    replay = json.loads((HERE / "results" / "replay_ref.json").read_text(encoding="utf-8"))
    for g in replay["groups"]:
        for jd, want, parent_line in (("B03_en_warehouse_supervisor", 9, 19), ("B07_ar_accountant", 10, 21)):
            for a in replay["groups"][g].get(jd, []):
                assert a["coverage"]["requirement_units"] == want, "children and the parent add no requirement"
                assert all(sp["first_line"] != parent_line for blk in a["uncovered"].values() for sp in blk)


def test_a_child_quoted_in_the_expected_list_is_met_and_in_another_list_is_misrouted_and_absent_is_missing():
    import replay_ref as RR
    c04 = CHILD_BY_ID["RQ-029/C04"]
    text = c04["child_text"]
    jd = (HERE / "jd" / "B03_en_warehouse_supervisor.txt").read_bytes().decode("utf-8")
    met = json.dumps({"categories": {}, "non_scoreable_requirements": [], "post_hiring_conditions": [{"source_text": text}],
                      "informational_items": []}, ensure_ascii=False)
    misrouted = json.dumps({"categories": {}, "non_scoreable_requirements": [{"source_text": text}], "post_hiring_conditions": [],
                            "informational_items": []}, ensure_ascii=False)
    missing = json.dumps({"categories": {}, "non_scoreable_requirements": [], "post_hiring_conditions": [], "informational_items": []})
    status = lambda raw: next(o["status"] for o in RR.child_obligations("B03_en_warehouse_supervisor", jd, raw) if o["child_id"] == "RQ-029/C04")  # noqa: E731
    assert status(met) == "met" and status(misrouted) == "misrouted" and status(missing) == "missing"


def test_a_partly_quoted_or_child_is_partial_not_met():
    import replay_ref as RR
    jd = (HERE / "jd" / "B03_en_warehouse_supervisor.txt").read_bytes().decode("utf-8")
    partial = json.dumps({"categories": {}, "non_scoreable_requirements": [{"source_text": "valid UAE residence visa"}],
                          "post_hiring_conditions": [], "informational_items": []})
    got = next(o for o in RR.child_obligations("B03_en_warehouse_supervisor", jd, partial) if o["child_id"] == "RQ-029/C03")
    assert got["status"] != "met" and got["fraction_expected_list"] < RR.C.COVER_THRESHOLD


def test_the_child_status_report_states_that_no_child_is_a_scored_criterion():
    md = (HERE / "results" / "replay_report.md").read_text(encoding="utf-8")
    assert "never a scored criterion" in md and "RQ-029/C03" in md


# ── RQ-030 child decisions ─────────────────────────────────────────────────────────────────────────────────────────────────
CHILD30 = [r for r in csv.DictReader((HERE / "reviews" / "CHILD_CONFIRMED_queue_Asaad-Hijjah_2026-10-15_RQ-030.csv").open(encoding="utf-8"))]


def test_the_three_rq_030_child_decisions_are_recorded_as_given():
    assert {r["child_id"]: r["confirmed_role"] for r in CHILD30} == {
        "RQ-030/C01": "non-scoreable condition", "RQ-030/C02": "non-scoreable condition", "RQ-030/C03": "informational benefit"}
    assert all(r["reviewer"] == "Asaad Hijjah" and r["confirmed_date"] == "2026-10-15" for r in CHILD30)


def test_s003_and_s011_apply_exactly_to_c01_and_c02_from_exact_offsets():
    import record_child_decisions as RC
    children = RC.load_children()
    for judgment in ("S003", "S011"):
        RC.check_judgment_scope(children, "RQ-030", judgment, ["RQ-030/C01", "RQ-030/C02"])
    assert all("S003 applies exactly to C01, C02" in r["judgment_scope_confirmed"] for r in CHILD30)
    with pytest.raises(SystemExit):
        RC.check_judgment_scope(children, "RQ-030", "S003", ["RQ-030/C01"])


def test_rq_030_is_resolved_by_children_and_its_uncertain_submission_is_preserved():
    assert QUEUE_BY_RQ["RQ-030"]["review_status"] == "resolved_by_children"
    assert SUBMISSION_BY_RQ["RQ-030"]["confirmed_decision"] == "uncertain"
    assert SUBMISSION_BY_RQ["RQ-030"]["status"].startswith("unresolved")


def test_the_rq_030_obligations_are_expected_lists_and_not_scored():
    import replay_ref as RR
    assert RR.CHILD_OBLIGATION["non-scoreable condition"] == "non_scoreable_requirements"
    assert RR.CHILD_OBLIGATION["informational benefit"] == "informational_items"
    replay = json.loads((HERE / "results" / "replay_ref.json").read_text(encoding="utf-8"))
    for g in replay["groups"]:
        for a in replay["groups"][g].get("B07_ar_accountant", []):
            ob = {o["child_id"]: o for o in a["child_obligations"] if o["child_id"].startswith("RQ-030")}
            assert set(ob) == {"RQ-030/C01", "RQ-030/C02", "RQ-030/C03"}
            assert ob["RQ-030/C01"]["expected_list"] == ob["RQ-030/C02"]["expected_list"] == "non_scoreable_requirements"
            assert ob["RQ-030/C03"]["expected_list"] == "informational_items"
            assert a["coverage"]["requirement_units"] == 10
