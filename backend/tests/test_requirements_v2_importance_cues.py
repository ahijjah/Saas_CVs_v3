"""Requirements-v2 extraction (offline): how a Preferred classification is checked against the job description.

The AI's classification is ALWAYS preserved. A Preferred item whose cue is missing, absent from the job description,
or not tied to that item gets an item-specific "Needs review" warning; it is never turned into Required because of it.
A cue that merely exists somewhere else in the job description is not proof that it applies to the item.
Every scenario is tested in English and in Arabic. No model call, no database."""
from __future__ import annotations

import copy

import pytest

from services.requirements_v2 import CATEGORIES, validate_final
from services.requirements_v2.extraction import process_ai_output
from services.requirements_v2.extraction.parser import (
    CUE_MISSING, CUE_NOT_IN_JD, CUE_NOT_LINKED, IMPORTANCE_CUE_REVIEW_CODES, NEEDS_REVIEW,
)
from services.requirements_v2.extraction.text import cue_relationship, locate_span


def item(text, source, importance="preferred", cue=None, category="skills"):
    return category, {"text": text, "importance": importance, "importance_cue": cue, "source_text": source,
                      "origin": "stated", "alternatives": None, "experience": None}


def run(jd, *entries, weights=None):
    cats = {c: [] for c in CATEGORIES}
    for category, raw in entries:
        cats[category].append(raw)
    ai = {"scoreability": {"status": "scoreable", "reason": ""}, "categories": cats,
          "category_weights": weights or {c: 0 for c in CATEGORIES}, "non_scoreable_requirements": [],
          "post_hiring_conditions": [], "informational_items": [], "warnings": []}
    return process_ai_output(ai, jd), ai


def cue_issues(result):
    return [i for i in result.review if i.code in IMPORTANCE_CUE_REVIEW_CODES]


def assert_preserved(result, ai, *, importance="preferred"):
    """The classification, the AI output, the source wording and the original snapshot are all intact."""
    assert result.raw_ai_output == ai and result.raw_ai_output is not ai
    for category in CATEGORIES:
        for it in result.requirements["categories"][category]["items"]:
            assert it["importance"] == importance and (it["weight"] is None) == (importance == "preferred")
    assert result.original == result.requirements and result.original is not result.requirements


# language-specific material --------------------------------------------------------------------------------------

EN = {
    "jd_other": "Requirements: Python is required. SQL is a plus.",
    "item_other": ("Python", "Python is required"), "cue_other": "is a plus",
    "jd_one": "Requirements: Python. Docker is a plus.",
    "inline": ("Docker", "Docker is a plus", "is a plus"),
    "jd_heading": "Requirements:\n- Python\n\nNice to have:\n- Docker\n- Kubernetes\n",
    "heading_items": [("Docker", "Docker"), ("Kubernetes", "Kubernetes")], "heading_cue": "Nice to have",
    "before_heading": ("Python", "Python"),
    "absent_cue": "optional", "missing_text": ("Docker", "Docker is a plus"),
    "jd_two_sections": "Nice to have:\n- Docker\n\nRequirements:\n- Python\n",
    "inline_variants": [("Docker", "Docker is an advantage", "is an advantage"),
                        ("Kubernetes", "Ideally Kubernetes", "Ideally")],
    "jd_inline_variants": "Docker is an advantage. Ideally Kubernetes.",
}
AR = {
    "jd_other": "المتطلبات: الخبرة في بايثون مطلوبة. يفضل معرفة SQL.",
    "item_other": ("بايثون", "الخبرة في بايثون مطلوبة"), "cue_other": "يفضل",
    "jd_one": "المتطلبات: بايثون. يفضل معرفة Docker.",
    "inline": ("Docker", "يفضل معرفة Docker", "يفضل"),
    "jd_heading": "المتطلبات:\n- بايثون\n\nمهارات إضافية (يفضل):\n- Docker\n- Kubernetes\n",
    "heading_items": [("Docker", "Docker"), ("Kubernetes", "Kubernetes")], "heading_cue": "يفضل",
    "before_heading": ("بايثون", "بايثون"),
    "absent_cue": "اختياري", "missing_text": ("Docker", "يفضل معرفة Docker"),
    "jd_two_sections": "مهارات إضافية (يفضل):\n- Docker\n\nالمتطلبات:\n- بايثون\n",
    "inline_variants": [("شهادة CPA", "تعتبر شهادة CPA ميزة إضافية", "ميزة إضافية"),
                        ("Docker", "ويُفضَّل Docker", "ويفضل")],
    "jd_inline_variants": "تعتبر شهادة CPA ميزة إضافية. ويُفضَّل Docker.",
}
LANGS = pytest.mark.parametrize("L", [EN, AR], ids=["english", "arabic"])


# ══ 1. missing preferred cue ══════════════════════════════════════════════════

class TestMissingCue:

    @LANGS
    @pytest.mark.parametrize("cue", [None, "", "   ", 7, ["x"]])
    def test_preferred_is_preserved_and_flagged_item_specifically(self, L, cue):
        text, source = L["missing_text"]
        r, ai = run(L["jd_one"], item(text, source, cue=cue), weights={"skills": 100})
        assert_preserved(r, ai)
        flagged = r.requirements["categories"]["skills"]["items"][0]
        issues = cue_issues(r)
        assert [i.code for i in issues] == [CUE_MISSING]
        assert issues[0].item_id == flagged["id"] and issues[0].category == "skills"
        assert issues[0].message.startswith(NEEDS_REVIEW) and "skills[0]" in issues[0].message
        assert r.item_review[flagged["id"]] == tuple(issues)

    @LANGS
    def test_the_wording_in_the_source_text_is_not_used_to_invent_a_cue(self, L):
        """'a plus' / 'يفضل' is inside the quoted wording, but the AI named no cue: still flagged, still Preferred."""
        text, source = L["missing_text"]
        r, _ = run(L["jd_one"], item(text, source, cue=None))
        assert [i.code for i in cue_issues(r)] == [CUE_MISSING]
        assert r.requirements["categories"]["skills"]["items"][0]["source_text"] is not None

    @LANGS
    def test_only_the_flagged_item_is_flagged(self, L):
        text, source, cue = L["inline"]
        r, _ = run(L["jd_one"], item(text, source, cue=cue), item(text + " 2", source, cue=None))
        flagged = [i.item_id for i in cue_issues(r)]
        assert flagged == [r.requirements["categories"]["skills"]["items"][1]["id"]]


# ══ 2. cue absent from the job description ════════════════════════════════════

class TestCueAbsentFromJd:

    @LANGS
    def test_preferred_is_preserved_and_flagged(self, L):
        text, source = L["missing_text"]
        r, ai = run(L["jd_one"], item(text, source, cue=L["absent_cue"]), weights={"skills": 100})
        assert_preserved(r, ai)
        (issue,) = cue_issues(r)
        assert issue.code == CUE_NOT_IN_JD and issue.message.startswith(NEEDS_REVIEW) and L["absent_cue"] in issue.message
        assert issue.item_id == r.requirements["categories"]["skills"]["items"][0]["id"]

    @LANGS
    def test_a_cue_that_is_not_verbatim_is_treated_as_absent(self, L):
        text, source, cue = L["inline"]
        r, _ = run(L["jd_one"], item(text, source, cue=cue + " x"))
        assert [i.code for i in cue_issues(r)] == [CUE_NOT_IN_JD]


# ══ 3. cue belonging to another requirement ═══════════════════════════════════

class TestCueBelongsToAnotherRequirement:

    @LANGS
    def test_a_cue_elsewhere_in_the_jd_is_not_proof_for_this_item(self, L):
        text, source = L["item_other"]
        r, ai = run(L["jd_other"], item(text, source, cue=L["cue_other"]), weights={"skills": 100})
        assert_preserved(r, ai)                                            # still Preferred: not downgraded to Required
        (issue,) = cue_issues(r)
        assert issue.code == CUE_NOT_LINKED and issue.message.startswith(NEEDS_REVIEW)
        assert "another requirement" in issue.message
        flagged = r.requirements["categories"]["skills"]["items"][0]
        assert flagged["importance"] == "preferred" and flagged["source_text"] == source      # wording untouched

    @LANGS
    def test_the_item_that_really_carries_the_cue_is_not_flagged(self, L):
        text, source = L["item_other"]
        other = (L["jd_other"].split(". ")[-1]).rstrip(".")
        r, _ = run(L["jd_other"], item(text, source, cue=L["cue_other"]),
                   item("SQL", other, cue=L["cue_other"]))
        assert [i.item_id for i in cue_issues(r)] == [r.requirements["categories"]["skills"]["items"][0]["id"]]

    @LANGS
    def test_a_heading_of_another_section_does_not_govern_the_item(self, L):
        """Nice-to-have heading above, but the item sits under a later 'Requirements' heading."""
        text, source = L["before_heading"]
        r, ai = run(L["jd_two_sections"], item(text, source, cue=L["heading_cue"]))
        assert_preserved(r, ai)
        assert [i.code for i in cue_issues(r)] == [CUE_NOT_LINKED]

    @LANGS
    def test_an_item_listed_before_the_preferred_heading_is_not_governed_by_it(self, L):
        text, source = L["before_heading"]
        r, _ = run(L["jd_heading"], item(text, source, cue=L["heading_cue"]))
        assert [i.code for i in cue_issues(r)] == [CUE_NOT_LINKED]

    @LANGS
    def test_unlocatable_source_wording_means_the_relationship_cannot_be_established(self, L):
        text, _ = L["missing_text"]
        r, _ = run(L["jd_one"], item(text, "wording that is not in the job description", cue=L["inline"][2]))
        assert sorted(i.code for i in cue_issues(r)) == [CUE_NOT_LINKED]
        assert "source_text_not_found" in [i.code for i in r.review]
        flagged = r.requirements["categories"]["skills"]["items"][0]
        assert flagged["importance"] == "preferred" and flagged["source_text"] is None


# ══ 4. preferred section heading governs the item ═════════════════════════════

class TestPreferredHeading:

    @LANGS
    def test_every_item_under_the_heading_is_established_without_warnings(self, L):
        entries = [item(t, s, cue=L["heading_cue"]) for t, s in L["heading_items"]]
        r, ai = run(L["jd_heading"], *entries, weights={"skills": 100})
        assert_preserved(r, ai)
        assert cue_issues(r) == [] and r.item_review == {}
        assert [i["weight"] for i in r.requirements["categories"]["skills"]["items"]] == [None, None]

    @LANGS
    def test_a_heading_without_a_colon_also_governs(self, L):
        jd = L["jd_heading"].replace(":", "")
        entries = [item(t, s, cue=L["heading_cue"]) for t, s in L["heading_items"]]
        r, _ = run(jd, *entries)
        assert cue_issues(r) == []

    @LANGS
    def test_plain_paragraph_lines_under_a_heading_are_governed_too(self, L):
        jd = L["jd_heading"].replace("- ", "")
        entries = [item(t, s, cue=L["heading_cue"]) for t, s in L["heading_items"]]
        r, _ = run(jd, *entries)
        assert cue_issues(r) == []

    @LANGS
    def test_the_cue_before_the_item_on_the_same_line_counts(self, L):
        cue = L["heading_cue"]
        jd = f"{cue}: Docker, Kubernetes"
        r, _ = run(jd, item("Docker", "Docker", cue=cue))
        assert cue_issues(r) == []

    @LANGS
    def test_a_governed_item_in_a_mixed_job_is_preferred_while_required_items_are_weighted(self, L):
        entries = [item(*L["before_heading"], importance="required"), *[item(t, s, cue=L["heading_cue"]) for t, s in L["heading_items"]]]
        r, ai = run(L["jd_heading"], *entries, weights={"skills": 100})
        items = r.requirements["categories"]["skills"]["items"]
        assert [i["importance"] for i in items] == ["required", "preferred", "preferred"]
        assert [i["weight"] for i in items] == [100, None, None] and cue_issues(r) == []
        assert validate_final(r.requirements).ok and r.readiness.state == "ready"


# ══ 5. valid inline preferred wording ═════════════════════════════════════════

class TestInlineWording:

    @LANGS
    def test_inline_cue_inside_the_items_own_wording_is_established(self, L):
        text, source, cue = L["inline"]
        r, ai = run(L["jd_one"], item(text, source, cue=cue), weights={"skills": 100})
        assert_preserved(r, ai)
        assert cue_issues(r) == [] and r.item_review == {}

    @LANGS
    def test_several_inline_phrasings_and_diacritic_insensitive_cues(self, L):
        r, _ = run(L["jd_inline_variants"], *[item(t, s, cue=c) for t, s, c in L["inline_variants"]])
        assert cue_issues(r) == []
        assert [i["importance"] for i in r.requirements["categories"]["skills"]["items"]] == ["preferred", "preferred"]

    @LANGS
    def test_required_items_ignore_any_cue_and_unspecified_importance_still_defaults_to_required(self, L):
        text, source, cue = L["inline"]
        required = item(text, source, importance="required", cue=cue)
        unspecified = item(text + " 2", source)
        del unspecified[1]["importance"]
        r, _ = run(L["jd_one"], required, unspecified, weights={"skills": 100})
        its = r.requirements["categories"]["skills"]["items"]
        assert [i["importance"] for i in its] == ["required", "required"] and [i["weight"] for i in its] == [50, 50]
        assert [i.code for i in r.review] == ["importance_missing_defaulted_required"]


# ══ the rest of the pipeline is unchanged ═════════════════════════════════════

class TestNothingElseChanged:

    @LANGS
    def test_flagged_preferred_items_follow_the_normal_weighting_rules(self, L):
        text, source = L["missing_text"]
        r, _ = run(L["jd_one"], item("Required one", "Required one", importance="required"),
                   item(text, source, cue=None), weights={"skills": 60, "experience": 40})
        c = r.requirements["categories"]
        assert [i["weight"] for i in c["skills"]["items"]] == [100, None]
        assert c["skills"]["weight"] == 100 and c["experience"]["weight"] == 0          # experience has no required item
        assert validate_final(r.requirements).ok

    @LANGS
    def test_preferred_only_job_with_flagged_items_still_needs_confirmation(self, L):
        text, source = L["missing_text"]
        r, _ = run(L["jd_one"], item(text, source, cue=None))
        assert r.readiness.state == "needs_confirmation"
        assert all(r.requirements["categories"][c]["weight"] == 0 for c in CATEGORIES)

    @LANGS
    def test_the_review_warnings_travel_in_the_analysis_envelope(self, L):
        text, source = L["missing_text"]
        r, _ = run(L["jd_one"], item(text, source, cue=None), weights={c: 0 for c in CATEGORIES})
        envelope = r.analysis["extraction"]["review"]
        assert [e["code"] for e in envelope] == [CUE_MISSING] and envelope[0]["item_id"]
        assert envelope[0]["message"].startswith(NEEDS_REVIEW)

    @LANGS
    def test_the_original_snapshot_keeps_the_ai_classification(self, L):
        text, source = L["missing_text"]
        r, _ = run(L["jd_one"], item(text, source, cue=None))
        assert r.original["categories"]["skills"]["items"][0]["importance"] == "preferred"

    def test_the_parser_no_longer_contains_the_downgrade_rule(self):
        import services.requirements_v2.extraction.parser as parser
        source = open(parser.__file__, encoding="utf-8").read()
        assert "preferred_not_supported_by_job_description" not in source

    def test_unspecified_importance_still_defaults_to_required_and_is_the_only_defaulting(self):
        raw = item("Docker", "Docker is a plus", cue="is a plus")[1]
        del raw["importance"]
        r, _ = run("Docker is a plus", ("skills", raw), weights={"skills": 100})
        assert r.requirements["categories"]["skills"]["items"][0]["importance"] == "required"
        assert [i.code for i in r.review] == ["importance_missing_defaulted_required"]


# ══ the relationship helper on its own ════════════════════════════════════════

class TestCueRelationship:

    def _rel(self, jd, quote, cue):
        return cue_relationship(jd, locate_span(jd, quote), cue)

    def test_inline_heading_and_none(self):
        jd = "Intro\nRequirements:\n- Python\nNice to have:\n- Docker is great\n- Go\n"
        assert self._rel(jd, "Docker is great", "great") == "inline"
        assert self._rel(jd, "Go", "Nice to have") == "heading"
        assert self._rel(jd, "Python", "Nice to have") is None
        assert self._rel(jd, "Python", "Requirements") == "heading"

    def test_numbered_lists_and_blank_lines_between_heading_and_items(self):
        jd = "Preferred:\n\n1. Docker\n2) Go\n"
        assert self._rel(jd, "Docker", "Preferred") == "heading" and self._rel(jd, "Go", "Preferred") == "heading"

    def test_arabic_indic_numbering_and_bullets(self):
        jd = "يفضل:\n١. Docker\n• Go\n"
        assert self._rel(jd, "Docker", "يفضل") == "heading" and self._rel(jd, "Go", "يفضل") == "heading"

    def test_no_heading_at_all(self):
        jd = "Python is required. Docker is a plus."
        assert self._rel(jd, "Python is required", "is a plus") is None
        assert self._rel(jd, "Docker is a plus", "is a plus") == "inline"

    def test_sentences_are_not_headings(self):
        jd = "Nice to have skills are listed below.\n- Docker\n"
        assert self._rel(jd, "Docker", "Nice to have") is None
