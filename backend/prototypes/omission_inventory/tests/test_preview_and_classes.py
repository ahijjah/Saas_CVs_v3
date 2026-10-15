"""The preview is the pinned baseline plus one marked addition; uncovered spans get exactly one class; every supported list is read."""
import json

import coverage as C
import inventory as I
import preview as P
import replay as R

JD, LABELS = R.load_jd()
INV = I.build_inventory(JD, json.loads(R.ANNOTATION.read_text(encoding="utf-8")))
TEXT = P.render(JD, INV)


def test_the_preview_uses_the_pinned_baseline_and_has_one_marked_addition():
    assert TEXT.count(P.ADD_BEGIN) == 1 and TEXT.count(P.ADD_END) == 1
    assert TEXT.count("##### SYSTEM (pinned baseline") == 1
    user_part = TEXT.split("##### USER", 1)[1]
    assert user_part.split(P.ADD_BEGIN)[0].rstrip("\n").endswith(JD.rstrip("\n").split("\n")[-1]) or "JD>>>" in user_part


def test_the_preview_adds_no_output_field():
    assert "unit_decisions" not in TEXT
    assert "no field is added" in TEXT


def test_the_addition_lists_every_unit_once_in_jd_order():
    block = TEXT.split(P.ADD_BEGIN, 1)[1].split(P.ADD_END, 1)[0]
    ids = [line[1:line.index("]")] for line in block.splitlines() if line.startswith("[")]
    assert ids == [u.unit_id for u in INV.units], "JD order, every unit once"
    assert len(ids) == len(INV.units)


def test_the_jd_text_in_the_preview_is_the_original_text_unchanged():
    assert JD in TEXT


def intro_jd():
    return "It is expected that the work is done on site.\n"


def test_an_opening_lead_in_left_out_is_classed_as_intro_wording():
    jd = intro_jd()
    inv = I.build_inventory(jd)
    rep = C.cover(inv, jd, ["work is done on site."])
    unit = rep["units"][0]
    sp = unit["uncovered_spans"][0]
    assert sp["text"] == "It is expected that the"
    assert R.classify_span(sp, unit, [])["class"] == "intro_wording"


def test_a_missing_clause_with_a_restating_quote_is_classed_as_paraphrase():
    jd = "Candidates must hold a current driving licence for the vehicle categories listed in Appendix A.\n"
    inv = I.build_inventory(jd)
    rep = C.cover(inv, jd, ["Candidates should hold a current driving licence for the vehicle categories listed in Appendix"])
    unit = rep["units"][0]
    not_verbatim = [v["quote"] for v in rep["quotes"] if v["status"] == "not_verbatim"]
    sp = unit["uncovered_spans"][0]
    assert R.classify_span(sp, unit, not_verbatim)["class"] == "paraphrase"


def test_a_weak_paraphrase_below_the_share_threshold_stays_a_genuine_omission_candidate():
    """The paraphrase rule is a word-share threshold (0.6). A loose restatement that shares fewer content words is NOT credited as
    a paraphrase; it is reported as a genuine omission candidate, and the README says so."""
    jd = "Candidates must hold a current driving licence for the vehicle categories listed in Appendix A.\n"
    inv = I.build_inventory(jd)
    rep = C.cover(inv, jd, ["Applicants should have a valid driving licence for the vehicle categories in Appendix"])
    unit = rep["units"][0]
    not_verbatim = [v["quote"] for v in rep["quotes"] if v["status"] == "not_verbatim"]
    assert R.classify_span(unit["uncovered_spans"][0], unit, not_verbatim)["class"] == "genuine_omission"


def test_a_missing_clause_with_no_matching_quote_is_a_genuine_omission_candidate():
    jd = "Candidates must hold a current driving licence for the vehicle categories listed in Appendix A.\n"
    inv = I.build_inventory(jd)
    rep = C.cover(inv, jd, [])
    unit = rep["units"][0]
    assert R.classify_span(unit["uncovered_spans"][0], unit, [])["class"] == "genuine_omission"


def test_an_intro_span_with_a_non_lead_in_word_is_not_intro_wording():
    unit = {"first_offset": 0}
    sp = {"start": 0, "end": 9, "text": "Candidate"}
    assert R.classify_span(sp, unit, [])["class"] == "genuine_omission"


def test_post_hiring_conditions_are_read_as_quotes():
    raw = json.dumps({"categories": {"skills": []}, "non_scoreable_requirements": [{"source_text": "Salary: USD 900"}],
                      "post_hiring_conditions": [{"source_text": "A background check is required before start."}],
                      "informational_items": [{"source_text": "We offer lunch"}]})
    quotes = R.answer_quotes(raw)
    assert "A background check is required before start." in quotes
    assert set(quotes) == {"Salary: USD 900", "A background check is required before start.", "We offer lunch"}


def test_a_post_hiring_quote_covers_its_unit():
    jd = "A background check is required before the start date.\n"
    inv = I.build_inventory(jd)
    raw = json.dumps({"post_hiring_conditions": [{"source_text": "A background check is required before the start date."}]})
    rep = C.cover(inv, jd, R.answer_quotes(raw))
    assert rep["summary"]["covered"] == 1 and rep["summary"]["all_text_quoted"] == 1


def test_a_missing_final_full_stop_is_punctuation_only_and_not_a_clause():
    jd = "Support the rollout of the new service to all regional offices.\n"
    inv = I.build_inventory(jd)
    rep = C.cover(inv, jd, ["Support the rollout of the new service to all regional offices"])
    u = rep["units"][0]
    assert u["status"] == "covered" and u["all_text_quoted"] is False, "the full stop is not quoted"
    assert u["clause_review_required"] is False, "punctuation alone does not need clause review"
    assert R.classify_span(u["uncovered_spans"][0], u, [])["class"] == "punctuation_only"


def test_an_opening_lead_in_with_the_job_title_is_intro_wording_only_when_the_title_is_passed():
    jd = "It is expected that the Technical Coordinator will work on site.\n"
    inv = I.build_inventory(jd)
    rep = C.cover(inv, jd, ["work on site."])
    u = rep["units"][0]
    sp = u["uncovered_spans"][0]
    assert sp["text"] == "It is expected that the Technical Coordinator will"
    assert R.classify_span(sp, u, [], frozenset({"technical", "coordinator"}))["class"] == "intro_wording"
    assert R.classify_span(sp, u, [])["class"] == "genuine_omission", "without the job title the phrase is not a lead-in"


def test_a_long_or_content_bearing_opening_span_is_never_intro_wording():
    jd = "Candidates must hold a licence for the vehicle categories in Appendix A on site.\n"
    inv = I.build_inventory(jd)
    rep = C.cover(inv, jd, ["on site."])
    u = rep["units"][0]
    sp = u["uncovered_spans"][0]
    assert R.classify_span(sp, u, [], frozenset())["class"] == "genuine_omission"
