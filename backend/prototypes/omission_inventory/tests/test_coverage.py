"""Coverage rules: union of quoted spans; a short quote cannot cover a whole unit; merged quotes overlap each unit; duplicates are ambiguous."""
import pytest

import coverage as C
import inventory as I

LONG = "Coordinate with MoNE teams and the ABRS vendor to identify, track, and resolve system, operational, and integration issues."
NEXT = "Participate in functional, integration, and user acceptance testing activities."


def inv_of(jd, **ann):
    return I.build_inventory(jd, {"jd_sha256": I.sha256_text(jd), **ann} if ann else None)


def status_of(report, fragment):
    return [u["status"] for u in report["units"] if fragment in u["text"]]


def test_a_full_quote_covers_its_unit():
    jd = LONG + "\n" + NEXT + "\n"
    rep = C.cover(inv_of(jd), jd, [LONG, NEXT])
    assert rep["summary"] == {"covered": 2, "partial": 0, "ambiguous": 0, "omitted": 0, "requirement_units": 2}


def test_no_quotes_means_every_requirement_unit_is_omitted():
    jd = LONG + "\n" + NEXT + "\n"
    rep = C.cover(inv_of(jd), jd, [])
    assert rep["summary"]["omitted"] == 2 and rep["summary"]["covered"] == 0


def test_a_short_quote_does_not_cover_a_long_unit():
    jd = LONG + "\n"
    rep = C.cover(inv_of(jd), jd, ["vendor"])
    assert status_of(rep, "Coordinate") == ["partial"]
    assert rep["summary"]["covered"] == 0


def test_overlapping_quotes_count_once_in_the_union():
    jd = LONG + "\n"
    first = LONG[: int(len(LONG) * 0.6)]
    second = LONG[int(len(LONG) * 0.3):]          # overlaps the first; the union is the whole unit
    rep = C.cover(inv_of(jd), jd, [first, second])
    assert status_of(rep, "Coordinate") == ["covered"]


def test_two_halves_that_do_not_reach_the_threshold_stay_partial():
    jd = LONG + "\n"
    half = LONG[: int(len(LONG) * 0.5)]
    rep = C.cover(inv_of(jd), jd, [half, half])    # the same half twice: the union is still half
    assert status_of(rep, "Coordinate") == ["partial"]


def test_a_valid_split_of_one_line_into_two_items_covers_it():
    cut = LONG.index("identify")
    jd = LONG + "\n"
    rep = C.cover(inv_of(jd), jd, [LONG[:cut], LONG[cut:]])
    assert status_of(rep, "Coordinate") == ["covered"]


def test_a_merged_quote_spanning_a_wrap_covers_the_whole_unit_it_overlaps():
    jd = "Ability to communicate with users\nand support their resolution.\nSupport the rollout.\n"
    inv = inv_of(jd)
    merged = "Ability to communicate with users\nand support their resolution."
    rep = C.cover(inv, jd, [merged, "Support the rollout."])
    assert [u["status"] for u in rep["units"]] == ["covered", "covered"]
    assert rep["quotes"][0]["status"] == "unique"


def test_a_merged_quote_that_leaves_out_the_start_of_a_wrapped_unit_is_partial_not_covered():
    jd = "Ability to communicate with users\nand support their resolution.\n"
    rep = C.cover(inv_of(jd), jd, ["communicate with users\nand support their resolution."])
    assert [u["status"] for u in rep["units"]] == ["partial"]


def test_a_quote_joined_with_a_space_instead_of_the_line_break_is_not_credited_but_is_flagged():
    jd = "Ability to communicate with users\nand support their resolution.\n"
    rep = C.cover(inv_of(jd), jd, ["Ability to communicate with users and support their resolution."])
    assert rep["summary"]["covered"] == 0 and rep["summary"]["omitted"] == 1
    v = rep["quotes"][0]
    assert v["status"] == "not_verbatim" and v["whitespace_variant"] is True


def test_a_repeated_identical_jd_line_is_ambiguous_and_is_not_credited_by_any_occurrence():
    jd = "Attend the weekly meeting.\nOther work here.\nAttend the weekly meeting.\n"
    rep = C.cover(inv_of(jd), jd, ["Attend the weekly meeting."])
    assert rep["quote_counts"]["ambiguous"] == 1
    assert [u["status"] for u in rep["units"] if "weekly" in u["text"]] == ["ambiguous", "ambiguous"]
    assert rep["summary"]["covered"] == 0 and rep["summary"]["omitted"] == 1, "the unrelated unit is still omitted"


def test_the_same_quote_listed_twice_in_an_answer_is_credited_once_and_reported_as_repeated():
    jd = "Attend the weekly meeting.\n"
    rep = C.cover(inv_of(jd), jd, ["Attend the weekly meeting.", "Attend the weekly meeting."])
    assert rep["summary"]["covered"] == 1 and rep["repeated_quotes_in_answer"] == 2


def test_a_paraphrase_is_not_credited():
    jd = "It is expected that the Technical Coordinator will work at the MoNE office in Ramallah.\n"
    rep = C.cover(inv_of(jd), jd, ["The Coordinator will work at the MoNE office in Ramallah."])
    assert rep["quote_counts"]["not_verbatim"] == 1 and rep["summary"]["omitted"] == 1


@pytest.mark.parametrize("bad", [None, "", "   ", 5, ["list"]])
def test_an_invalid_quote_is_reported_and_never_credited(bad):
    jd = "Attend the weekly meeting.\n"
    rep = C.cover(inv_of(jd), jd, [bad])
    assert rep["quote_counts"]["invalid"] == 1 and rep["summary"]["covered"] == 0


def test_an_ambiguous_quote_routes_an_otherwise_uncovered_unit_to_review_not_omission():
    jd = "Attend the weekly meeting.\nAttend the weekly meeting.\n"
    rep = C.cover(inv_of(jd), jd, ["Attend the weekly meeting."])
    assert [u["status"] for u in rep["units"]] == ["ambiguous", "ambiguous"]


def test_a_jd_that_does_not_match_the_inventory_is_refused():
    jd = "Attend the weekly meeting.\n"
    with pytest.raises(ValueError, match="does not match"):
        C.cover(inv_of(jd), jd + " ", [])


def test_arabic_quotes_are_credited_by_exact_match_only():
    jd = "إعداد التقارير الشهرية للإدارة العليا\n"
    rep = C.cover(inv_of(jd), jd, ["إعداد التقارير الشهرية للإدارة العليا"])
    assert rep["summary"]["covered"] == 1
    rep2 = C.cover(inv_of(jd), jd, ["إعداد التقارير الشهرية"])
    assert rep2["summary"]["covered"] == 0 and rep2["summary"]["partial"] == 1
