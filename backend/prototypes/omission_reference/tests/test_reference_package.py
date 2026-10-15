"""Offline tests of the multilingual reference package. No model is called; the stored answers and fixtures are only read."""
import csv
import hashlib
import json
import pathlib
import subprocess
import sys

import pytest

import build_package as B
import inventory as I
import replay_ref as RR

HERE = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
SHEET = list(csv.DictReader((HERE / "reviewer_sheet.csv").open(encoding="utf-8")))
REPLAY = json.loads((HERE / "results" / "replay_ref.json").read_text(encoding="utf-8"))


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── exact text and hashes ────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("jd_id", list(MANIFEST["jds"]))
def test_the_stored_jd_is_the_exact_fixture_text_and_its_recorded_hash(jd_id):
    meta = MANIFEST["jds"][jd_id]
    stored = (HERE / "jd" / f"{jd_id}.txt").read_bytes().decode("utf-8")
    assert sha(stored) == meta["jd_sha256"]
    src = next(e for e in B.JDS if e["id"] == jd_id)
    raw = src["source"].read_bytes()
    assert hashlib.sha256(raw).hexdigest() == meta["source_file_sha256"], "the fixture file itself is unchanged"
    assert json.loads(raw.decode("utf-8"))[src["field"]] == stored, "no normalisation: byte-for-byte the fixture string"


def test_the_package_covers_tc20_and_five_existing_jds_in_english_and_arabic():
    assert set(MANIFEST["jds"]) == {"TC20", "B02_en_data_analyst", "B03_en_warehouse_supervisor", "B07_ar_accountant",
                                    "B08_ar_hr_specialist", "B12_ar_injection"}
    langs = {meta["language"] for meta in MANIFEST["jds"].values()}
    assert langs == {"en", "ar"}


# ── deterministic units with exact offsets ───────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("jd_id", list(MANIFEST["jds"]))
def test_every_unit_offset_reproduces_its_text_in_the_original(jd_id):
    text = (HERE / "jd" / f"{jd_id}.txt").read_bytes().decode("utf-8")
    cand = json.loads((HERE / "annotations" / f"{jd_id}.candidate.json").read_text(encoding="utf-8"))
    headings = [l["text"].strip() for l in cand["lines"] if l["proposed_type"] == "heading"]
    background = [l["text"].strip() for l in cand["lines"] if l["proposed_type"] == "background"]
    inv = I.build_inventory(text, {"jd_sha256": sha(text), "headings": headings, "background": background})
    for u in inv.units:
        assert "\n".join(text[a:b] for a, b in u.spans) == u.text
    assert len(inv.units) == sum(1 for l in text.split("\n") if l.strip()), "one unit per non-blank line: no soft-wrap merge here"


@pytest.mark.parametrize("jd_id", list(MANIFEST["jds"]))
def test_the_reviewer_sheet_has_every_unit_of_every_jd_once(jd_id):
    rows = [r for r in SHEET if r["jd_id"] == jd_id]
    assert len(rows) == MANIFEST["jds"][jd_id]["units"]
    assert len({r["unit_id"] for r in rows}) == len(rows)


# ── candidate annotation: never presented as human-confirmed ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("jd_id", list(MANIFEST["jds"]))
def test_every_candidate_file_is_labelled_unconfirmed_and_covers_every_line_once(jd_id):
    cand = json.loads((HERE / "annotations" / f"{jd_id}.candidate.json").read_text(encoding="utf-8"))
    assert cand["status"] == "CLAUDE-GENERATED CANDIDATE - NOT HUMAN-CONFIRMED"
    text = (HERE / "jd" / f"{jd_id}.txt").read_bytes().decode("utf-8")
    nonblank = [n for n, l in enumerate(text.split("\n"), 1) if l.strip()]
    assert [l["first_line"] for l in cand["lines"]] == nonblank
    assert {l["proposed_type"] for l in cand["lines"]} <= {"requirement", "heading", "background", "uncertain"}
    assert all(l["reason"] for l in cand["lines"]), "every line has a reason"


def test_each_candidate_heading_and_background_line_is_an_exact_line_of_its_jd():
    for jd_id in MANIFEST["jds"]:
        text = (HERE / "jd" / f"{jd_id}.txt").read_bytes().decode("utf-8")
        lines = {l.strip() for l in text.split("\n")}
        cand = json.loads((HERE / "annotations" / f"{jd_id}.candidate.json").read_text(encoding="utf-8"))
        for l in cand["lines"]:
            if l["proposed_type"] in ("heading", "background"):
                assert l["text"].strip() in lines and l["text"] in text, (jd_id, l["text"][:40])


# ── reviewer decisions: only where an existing confirmed judgment applies exactly ───────────────────────────────────────────
def test_a_reviewer_decision_is_filled_only_for_a_whole_unit_confirmed_judgment():
    for r in SHEET:
        if r["reviewer_decision"]:
            assert "whole unit" in r["existing_confirmed_judgment"], r["unit_id"]
            assert "confirmed 2026-10-12 by Asaad Hijjah" in r["reviewer_decision"]
        else:
            assert "whole unit" not in r["existing_confirmed_judgment"] or r["reviewer_decision"] == "", r["unit_id"]


def test_the_expected_judgments_are_linked_with_the_right_scope():
    import re
    ids = lambda rows: {i for r in rows for i in re.findall(r"S\d{3}", r["existing_confirmed_judgment"])}  # noqa: E731
    decided = ids([r for r in SHEET if r["reviewer_decision"]])
    sentence_only = ids([r for r in SHEET if "sentence within unit" in r["existing_confirmed_judgment"] and not r["reviewer_decision"]])
    assert decided == {"S002", "S004", "S005", "S006", "S007", "S008"}
    assert sentence_only == {"S003", "S010", "S011"}


def test_the_superseded_s010_is_recorded_as_false_alarm_not_genuine_defect():
    rows = [r for r in SHEET if "S010" in r["existing_confirmed_judgment"]]
    assert rows and all("false alarm" in r["existing_confirmed_judgment"] for r in rows)
    assert all(not r["reviewer_decision"] for r in rows), "a sentence-level judgment leaves the decision blank"


def test_claude_proposed_types_are_never_written_into_the_decision_column():
    for r in SHEET:
        if r["reviewer_decision"]:
            assert r["reviewer_decision"].split(" [")[0] in {"genuine defect", "false alarm"}


# ── replay: groups kept apart, coverage and defects kept apart ──────────────────────────────────────────────────────────────
def test_groups_are_never_pooled_and_each_names_its_model_and_prompt():
    for g in REPLAY["groups"]:
        assert "| model " in g and "| prompt " in g, g
    names = list(REPLAY["groups"])
    assert len(names) == len(set(names))
    assert any("baseline_v2-1" in g for g in names) and any("v2-2_run1" in g for g in names)


def test_the_tc20_duty_omission_is_reported_as_lines_and_as_genuine_spans():
    g = next(k for k in REPLAY["groups"] if k.startswith("TC20 repeat") and "prompt v2-3" in k)
    answers = REPLAY["groups"][g]["TC20"]
    assert [a["defects"]["item_omissions"] for a in answers].count(16) == 1, "only call 5 loses all sixteen duty lines"
    lost = max(answers, key=lambda a: a["defects"]["item_omissions"])
    genuine = [sp for sp in lost["uncovered"]["requirement"] if sp["class"] == "genuine_omission"]
    assert len(genuine) >= 16


def test_an_and_split_defect_is_found_on_the_stored_b12_answer_and_matches_reviewer_s008():
    g = next(k for k in REPLAY["groups"] if "v2-2_run1" in k and "run run1" in k)
    b12 = REPLAY["groups"][g]["B12_ar_injection"][0]
    assert b12["defects"]["AND"] == 1
    assert b12["defects"]["detail"]["AND"][0]["expected_items"] == ["CSS", "HTML"]


def test_changing_only_an_or_list_changes_the_or_defect_and_not_the_coverage_block():
    """The coverage block and its uncovered spans depend only on quotes; a defect edit must leave them identical."""
    jds, cands = RR.load_package()
    labels = json.loads((RR.BACKEND / "tests" / "fixtures" / "requirements_v2_technical_coordinator_20" / "expected_labels.json").read_text(encoding="utf-8"))
    case = next(c for c in RR.E.load_cases() if c["id"] == "B02_en_data_analyst")
    rows = [json.loads(l) for l in (RR.BENCH_RUNS / "v2-2_run1" / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    rec = next(r for r in rows if r["case"] == "B02_en_data_analyst" and r["run"] == "run1")
    obj = json.loads(rec["raw"])
    for lst in obj["categories"].values():
        for it in lst:
            if it.get("alternatives"):
                it["alternatives"] = None
    edited = {**rec, "raw": json.dumps(obj, ensure_ascii=False)}
    args = ("B02_en_data_analyst", jds["B02_en_data_analyst"], cands["B02_en_data_analyst"], labels, case, RR.type_of_line(cands["B02_en_data_analyst"]))
    before = RR.replay_answer(rec, *args)
    after = RR.replay_answer(edited, *args)
    assert before["coverage"] == after["coverage"] and before["uncovered"] == after["uncovered"]
    assert after["defects"]["OR"] > before["defects"]["OR"]


# ── Arabic and fragments: classified separately, not as omissions ──────────────────────────────────────────────────────────
def _unit(first_offset, text):
    return {"first_offset": first_offset, "text": text}


def test_an_arabic_label_that_opens_a_unit_is_intro_wording_not_an_omission():
    unit = _unit(0, "الشروط: الراتب بالريال السعودي، والعمل في مدينة الرياض بدوام كامل.")
    assert RR.classify_ref({"start": 0, "end": 6, "text": "الشروط:"}, unit, [], frozenset()) == "intro_wording"


def test_a_lone_arabic_connector_is_a_fragment():
    unit = _unit(0, "x")
    assert RR.classify_ref({"start": 9, "end": 12, "text": "، و"}, unit, [], frozenset()) == "connector_fragment"


def test_a_real_arabic_clause_stays_a_genuine_omission_candidate():
    unit = _unit(0, "- خبرة في نظام SAP.")
    assert RR.classify_ref({"start": 2, "end": 18, "text": "خبرة في نظام SAP."}, unit, [], frozenset()) == "genuine_omission"


def test_a_bullet_marker_left_uncovered_is_punctuation_only():
    unit = _unit(0, "- x")
    assert RR.classify_ref({"start": 0, "end": 1, "text": "-"}, unit, [], frozenset()) == "punctuation_only"


# ── no model call, no network client ─────────────────────────────────────────────────────────────────────────────────────
def test_the_replay_loads_no_openai_or_http_client():
    code = ("import sys; sys.path[:0]=['.','../omission_inventory','../..','../../scripts']; import replay_ref; replay_ref.load_package(); "
            "print('openai' in sys.modules or 'httpx' in sys.modules)")
    out = subprocess.run([sys.executable, "-I", "-c", code], cwd=str(HERE), capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-1500:]
    assert out.stdout.strip().splitlines()[-1] == "False"
