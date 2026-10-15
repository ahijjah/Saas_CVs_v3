"""Inventory rules: units come from the original text; headings and background only from a reviewed annotation; soft wraps; Arabic; CRLF."""
import json

import pytest

import inventory as I
import replay as R

JD_TC20, LABELS = R.load_jd()
ANN = json.loads(R.ANNOTATION.read_text(encoding="utf-8"))


def synth(text, **ann):
    annotation = {"jd_sha256": I.sha256_text(text), **ann} if ann else None
    return I.build_inventory(text, annotation)


def test_every_unit_is_a_verbatim_slice_of_the_original_text():
    inv = I.build_inventory(JD_TC20, ANN)
    for u in inv.units:
        assert "\n".join(JD_TC20[a:b] for a, b in u.spans) == u.text, u.unit_id
    assert inv.jd_sha256 == LABELS["jd_sha256"]


def test_tc20_duty_and_competency_lines_are_requirement_units():
    inv = I.build_inventory(JD_TC20, ANN)
    reqs = {u.text.strip() for u in inv.requirements()}
    for line in LABELS["expected_values"]["duty_lines"] + LABELS["expected_values"]["competency_lines"]:
        assert line in reqs, line


def test_tc20_headings_and_background_are_not_requirements():
    inv = I.build_inventory(JD_TC20, ANN)
    excluded = {u.text.strip() for u in inv.units if u.kind != "requirement"}
    assert "Responsibilities" in excluded and "Skills and Competencies" in excluded
    assert "متطلبات الوظيفة" in excluded
    assert not any(t.startswith("The Coordinator will also contribute") for t in {u.text.strip() for u in inv.requirements()})


def test_a_stale_or_foreign_annotation_is_refused():
    with pytest.raises(ValueError, match="different JD"):
        I.build_inventory(JD_TC20, {"jd_sha256": "0" * 64, "headings": [], "background": []})
    with pytest.raises(ValueError, match="not in the JD"):
        I.build_inventory(JD_TC20, {"jd_sha256": LABELS["jd_sha256"], "headings": ["No such heading"], "background": []})


def test_a_short_unannotated_line_is_a_requirement_and_is_never_a_heading_by_shape():
    text = "Skills\nResponsibilities:\nTeach the team.\n"
    inv = synth(text)
    assert [u.kind for u in inv.units] == ["requirement", "requirement", "requirement"]


def test_a_line_is_a_heading_only_when_listed_exactly():
    text = "Qualifications and Professional Experience:\nRun the reports.\n"
    listed = synth(text, headings=["Qualifications and Professional Experience:"])
    assert [u.kind for u in listed.units] == ["heading", "requirement"]


def test_a_listed_heading_that_does_not_match_exactly_is_refused():
    text = "Qualifications and Professional Experience:\nRun the reports.\n"
    with pytest.raises(ValueError, match="not in the JD"):
        synth(text, headings=["Qualifications and Professional Experience"])


def test_a_soft_wrap_is_joined_into_one_unit_with_one_span_per_physical_line():
    text = "Ability to communicate with users\nand support their resolution.\nNext item.\n"
    inv = synth(text)
    assert len(inv.units) == 2
    first = inv.units[0]
    assert first.text == "Ability to communicate with users\nand support their resolution."
    assert len(first.spans) == 2 and inv.diagnostics["soft_wrap_merged_lines"] == [2]


def test_no_join_when_the_previous_line_ends_in_terminal_punctuation():
    inv = synth("Support the rollout.\nprovide training.\n")
    assert len(inv.units) == 2


def test_arabic_lines_are_separate_requirement_units_and_are_never_joined():
    text = "المهام:\nإعداد التقارير الشهرية\nمتابعة الشكاوى اليومية\n"
    inv = synth(text, headings=["المهام:"])
    assert [u.kind for u in inv.units] == ["heading", "requirement", "requirement"]
    assert inv.units[1].text == "إعداد التقارير الشهرية"


def test_an_arabic_wrapped_continuation_is_not_joined_so_it_is_a_separate_unit():
    text = "إعداد التقارير الشهرية للإدارة العليا\nوالتنسيق مع الفروع\n"
    inv = synth(text)
    assert len(inv.units) == 2, "Arabic has no case, so the rule never joins; the continuation stays a separate unit (a known limit)"


def test_crlf_text_keeps_offsets_into_the_original_and_units_have_no_carriage_returns():
    text = "Responsibilities\r\nSupport the rollout.\r\nProvide training.\r\n"
    inv = synth(text, headings=["Responsibilities"])
    for u in inv.units:
        for a, b in u.spans:
            assert "\r" not in text[a:b]
    assert [u.text for u in inv.requirements()] == ["Support the rollout.", "Provide training."]
    assert inv.diagnostics["crlf_lines"] == 3


def test_a_background_line_is_excluded_from_the_requirement_count():
    inv = synth("Background sentence here.\nDo the work.\n", background=["Background sentence here."])
    assert [u.kind for u in inv.units] == ["background", "requirement"]
    assert len(inv.requirements()) == 1


def test_a_line_listed_as_heading_and_background_is_refused():
    with pytest.raises(ValueError, match="both"):
        synth("Same line.\n", headings=["Same line."], background=["Same line."])
