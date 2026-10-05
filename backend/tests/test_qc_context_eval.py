"""Offline tests for the job-analysis qualifying-context evaluation harness (no API, no database)."""
import ast
import importlib.util
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
SCRIPT = BACKEND / "scripts" / "qc_context_eval.py"
_spec = importlib.util.spec_from_file_location("qc_context_eval", SCRIPT)
qe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qe)

MAIN = qe.load_cases(qe.MAIN_FIXTURE)
HELDOUT = qe.load_cases(qe.HELDOUT_FIXTURE)
ALL = MAIN + HELDOUT
BY_ID = {c["id"]: c for c in ALL}
S1_FIXTURES = [BACKEND / "scripts" / "s1_eval_fixtures" / n for n in ("s1_boundary_cases.json", "s1_heldout_cases.json")]


def scripted(plan: dict, runs: int = 1, cases=None):
    cases = cases or [BY_ID[c] for c in plan]
    records = qe.run_all(cases, qe.ScriptedResponder(plan), runs=runs)
    return records, qe.summarize(records, cases)


def qc(state, *contexts):
    return {"state": state, "contexts": list(contexts), "source": "analysis"}


# ── fixture schema and coverage ─────────────────────────────────────────────

class TestFixtureSchema:
    CASE_KEYS = {"id", "family", "lang", "description", "jd_lines", "analysis_stub", "expected", "decoys", "flags",
                 "oracle", "split", "fixture_version"}

    @pytest.mark.parametrize("path,split,version", [(qe.MAIN_FIXTURE, "main", "qc-main-1"),
                                                    (qe.HELDOUT_FIXTURE, "heldout", "qc-heldout-1")])
    def test_file_header_and_format(self, path, split, version):
        text = path.read_text(encoding="utf-8")
        fx = json.loads(text)
        assert set(fx) == {"_comment", "fixture_version", "split", "cases"}
        assert (fx["fixture_version"], fx["split"]) == (version, split)
        assert text == json.dumps(fx, ensure_ascii=False, indent=1) + "\n"      # stable on-disk format

    @pytest.mark.parametrize("case", ALL, ids=lambda c: c["id"])
    def test_case_shape(self, case):
        assert set(case) == self.CASE_KEYS
        assert case["family"] in qe.FAMILIES and case["lang"] in ("en", "ar")
        assert case["jd_lines"] and all(isinstance(ln, str) for ln in case["jd_lines"])
        stub = case["analysis_stub"]
        assert set(stub) == {"minimum_years", "relevant_roles"} and isinstance(stub["relevant_roles"], list)
        exp, orc = case["expected"], case["oracle"]
        assert exp["state"] in qe.STATES
        if exp["state"] == "uncertain":
            assert "contexts" not in exp and set(exp) <= {"state", "allowed_candidates"}
        else:
            assert set(exp) == {"state", "contexts"}
        assert set(orc) == qe.QC_KEYS and orc["source"] == "analysis" and orc["state"] == exp["state"]
        assert set(case["flags"]) <= {qe.FLAG_NO_CRITERION}

    @pytest.mark.parametrize("case", ALL, ids=lambda c: c["id"])
    def test_state_context_consistency(self, case):
        exp = case["expected"]
        if exp["state"] == "identified":
            assert exp["contexts"] and case["oracle"]["contexts"]
        elif exp["state"] == "none":
            assert exp["contexts"] == [] and case["oracle"]["contexts"] == []
        if case["decoys"]:
            assert exp["state"] == "none"

    def test_unique_ids_and_no_reused_requirement_lines(self):
        ids = [c["id"] for c in ALL]
        assert len(ids) == len(set(ids))
        assert all(c["id"].startswith("QC-HO-") for c in HELDOUT)
        assert not any(c["id"].startswith("QC-HO-") for c in MAIN)
        main_lines = {" ".join(qe.words(ln)) for c in MAIN for ln in c["jd_lines"] if ln.startswith("-")}
        ho_lines = {" ".join(qe.words(ln)) for c in HELDOUT for ln in c["jd_lines"] if ln.startswith("-")}
        assert not main_lines & ho_lines

    def test_counts_and_distribution(self):
        assert (len(MAIN), len(HELDOUT)) == (34, 21)
        assert Counter(c["family"] for c in MAIN) == Counter(
            {"A": 3, "B": 3, "C": 2, "D": 3, "E": 4, "F": 3, "G": 2, "H": 2, "I": 3, "J": 2, "K": 2, "L": 2, "M": 2,
             "N": 1})
        assert Counter(c["family"] for c in HELDOUT) == Counter(
            {"A": 2, "B": 2, "C": 1, "D": 2, "E": 2, "F": 2, "G": 2, "H": 1, "I": 2, "J": 1, "K": 1, "L": 1, "M": 1,
             "N": 1})
        assert Counter(c["lang"] for c in MAIN) == Counter({"en": 25, "ar": 9})
        assert Counter(c["lang"] for c in HELDOUT) == Counter({"en": 14, "ar": 7})
        assert Counter(c["expected"]["state"] for c in MAIN) == Counter({"identified": 19, "none": 11, "uncertain": 4})
        assert Counter(c["expected"]["state"] for c in HELDOUT) == Counter(
            {"identified": 12, "none": 7, "uncertain": 2})

    @pytest.mark.parametrize("cases", [MAIN, HELDOUT], ids=["main", "heldout"])
    def test_family_coverage_and_expected_states(self, cases):
        want = {"A": "identified", "B": "identified", "C": "identified", "D": "none", "E": "none", "F": "identified",
                "G": "none", "H": "identified", "I": "identified", "J": "uncertain", "K": "uncertain",
                "L": "identified", "M": "none", "N": "identified"}
        assert {c["family"] for c in cases} == set(qe.FAMILIES)
        for c in cases:
            assert c["expected"]["state"] == want[c["family"]], c["id"]
        assert all(c["lang"] == "ar" for c in cases if c["family"] in "FG")
        assert any(c["lang"] == "ar" for c in cases if c["family"] == "H")
        assert all(c["decoys"] for c in cases if c["family"] in "EGM")

    def test_family_i_has_contiguous_and_non_contiguous_conjunctions(self):
        for cases in (MAIN, HELDOUT):
            sizes = sorted(len(c["expected"]["contexts"]) for c in cases if c["family"] == "I")
            assert sizes[0] == 1 and sizes[-1] >= 2

    def test_family_l_context_absent_from_relevant_roles(self):
        for c in (c for c in ALL if c["family"] == "L"):
            roles = " ".join(" ".join(qe.words(r)) for r in c["analysis_stub"]["relevant_roles"])
            for slot in c["expected"]["contexts"]:
                assert not any(" ".join(qe.strip_leading(qe.words(v))) in roles for v in qe.variants(slot))

    def test_family_h_or_stays_one_phrase(self):
        for c in (c for c in ALL if c["family"] == "H"):
            (slot,) = c["expected"]["contexts"]
            assert {"or", "أو"} & set(qe.words(qe.variants(slot)[0]))


class TestFixtureGrounding:
    @pytest.mark.parametrize("case", ALL, ids=lambda c: c["id"])
    def test_every_labelled_phrase_is_verbatim_in_the_jd(self, case):
        jd = qe.JDText(qe.case_jd(case))
        exp = case["expected"]
        for slot in (exp.get("contexts") or []) + (exp.get("allowed_candidates") or []):
            for v in qe.variants(slot):
                assert qe.grounded(jd, v), (case["id"], v)
        for p in case["oracle"]["contexts"] + case["decoys"]:
            assert qe.grounded(jd, p), (case["id"], p)

    @pytest.mark.parametrize("case", ALL, ids=lambda c: c["id"])
    def test_one_of_is_narrow(self, case):
        exp = case["expected"]
        for slot in (exp.get("contexts") or []) + (exp.get("allowed_candidates") or []):
            assert qe.one_of_narrow(slot), (case["id"], slot)

    def test_one_of_guard_rejects_semantic_variants(self):
        assert qe.one_of_narrow({"one_of": ["banking sector", "the banking sector", "in the banking sector"]})
        assert not qe.one_of_narrow({"one_of": ["banking sector", "banking industry"]})
        assert not qe.one_of_narrow({"one_of": ["banking sector", "banks"]})
        assert not qe.one_of_narrow({"one_of": ["oil and gas projects", "oil projects"]})
        assert not qe.one_of_narrow({"one_of": ["القطاع المصرفي", "البنوك"]})

    def test_family_n_flags_match_todays_enumeration(self):
        for c in ALL:
            assert qe.enumeration_gap(c) == (qe.FLAG_NO_CRITERION in c["flags"]), c["id"]
        assert {c["id"] for c in ALL if c["flags"]} == {"QC-N1", "QC-HO-N1"}


# ── vocabulary separation (main vs held-out vs S1) ──────────────────────────

GENERIC = frozenset({
    "in", "on", "at", "within", "the", "a", "an", "of", "and", "or", "to", "with",
    "sector", "industry", "projects", "environment", "environments", "organisations", "companies", "team",
    "في", "ضمن", "لدى", "أو", "و", "القطاع", "مشاريع", "بيئة", "عمل", "فريق", "شركات",
})


def s1_phrases() -> set[str]:
    out = set()
    for path in S1_FIXTURES:
        for c in json.loads(path.read_text(encoding="utf-8"))["cases"]:
            out |= set(c["analysis"]["relevant_roles"])
            orc = c["oracle"]
            for r in orc.get("restrictions") or []:
                if r["kind"] != "vague":
                    out.add(r["text"])
            for t in orc.get("targets") or []:
                if t.get("jd_span"):
                    out.add(t["jd_span"]["text"])
                if "text" in t:
                    out.add(t["text"])
            if orc.get("setting"):
                out.add(orc["setting"]["text"])
    return out


def content_words(phrases) -> set[str]:
    return {w for p in phrases for w in qe.words(p)} - GENERIC


def _phrase_overlaps(a: set[str], b: set[str]) -> list[tuple[str, str]]:
    hits = []
    for p in a:
        pw = qe.words(p)
        for q in b:
            qw = qe.strip_leading(qe.words(q))
            if qw and set(qw) - GENERIC and qe._contains_run(pw, qw):
                hits.append((p, q))
    return sorted(hits)


class TestVocabularySeparation:
    MAIN_P, HO_P = qe.fixture_phrases(MAIN), qe.fixture_phrases(HELDOUT)

    def test_phrase_level_disjoint_both_directions(self):
        assert _phrase_overlaps(self.HO_P, self.MAIN_P) == []
        assert _phrase_overlaps(self.MAIN_P, self.HO_P) == []

    def test_content_words_disjoint_from_main(self):
        assert content_words(self.HO_P) & content_words(self.MAIN_P) == set()

    def test_heldout_disjoint_from_s1_fixture_vocabulary(self):
        s1 = s1_phrases()
        assert len(s1) > 30
        assert _phrase_overlaps(self.HO_P, s1) == [] and _phrase_overlaps(s1, self.HO_P) == []
        assert content_words(self.HO_P) & content_words(s1) == set()

    def test_heldout_requirement_lines_share_no_content_word_with_main_contexts(self):
        main_ctx = content_words(p for c in MAIN for p in c["oracle"]["contexts"] + c["decoys"])
        ho_text = {w for c in HELDOUT for ln in c["jd_lines"] for w in qe.words(ln)}
        assert main_ctx & ho_text == set()

    def test_checker_detects_overlap(self):
        assert _phrase_overlaps({"5 years in the banking sector"}, {"in the banking sector"})
        assert content_words({"retail banking sector"}) & content_words({"banking sector"}) == {"banking"}


# ── canonical-word reuse: pinned jd_text behaviour ──────────────────────────

class TestPinnedJDText:
    """Pins the S1 jd_text behaviour this harness relies on: a change there must fail here visibly."""

    def test_words_and_dotted_acronyms(self):
        assert qe.words("State-owned Companies") == ["state", "owned", "companies"]
        assert qe.words("P.M. role") == ["pm", "role"]

    @pytest.mark.parametrize("jd,phrase,ok", [
        ("خبرة كمحاسب في القطاع المصرفي.", "القطاع المصرفي", True),
        ("خبرة كمحاسب بالقطاع المصرفي.", "القطاع المصرفي", True),          # proclitic ب
        ("خبرة كمحاسب وبالقطاع المصرفي.", "القطاع المصرفي", True),         # proclitic chain وب
        ("خبرة كمحاسب في القطاعات المصرفية.", "القطاع المصرفي", False),     # suffix: right edge strict
        ("خبرة كمحاسب في القطاع المصرفية.", "القطاع المصرفي", False),
        ("خبرة كمحاسب في قطاع مصرفي.", "القطاع المصرفي", False),            # article is part of the word
        ("خبرة كمحاسب في القطاع المصرفي.", "قطاع المصرفي", False),          # ال is not a proclitic
        ("Experience in the Banking Sector.", "banking sector", True),       # case-insensitive
        ("Experience in the banking sectors.", "banking sector", False),
        ("Experience in investment banking sector roles.", "banking sector", True),
        ("Experience in e-banking sector.", "banking sector", True),        # hyphen is a word boundary
        ("Experience in the banking  sector.", "banking sector", True),      # whitespace collapsed
    ])
    def test_grounding_boundaries(self, jd, phrase, ok):
        assert qe.grounded(qe.JDText(jd), phrase) is ok

    def test_locate_words_proclitic_only_on_first_word(self):
        assert qe.locate_words(["القطاع", "المصرفي"], ["بالقطاع", "المصرفي"]) == [(0, "ب")]
        assert qe.locate_words(["القطاع", "المصرفي"], ["القطاع", "والمصرفي"]) == []
        assert qe.locate_words(["sector"], ["subsector"]) == []


# ── accepted spans and checks ────────────────────────────────────────────────

class TestPhraseMatching:
    SLOT = {"one_of": ["banking sector", "the banking sector", "in the banking sector"]}

    @pytest.mark.parametrize("actual,ok", [
        ("banking sector", True), ("The Banking Sector", True), ("in the banking sector", True),
        ("banking industry", False), ("banks", False), ("sector", False), ("banking", False),
        ("the banking sector in the region", False), ("within the banking sector", False),
    ])
    def test_english(self, actual, ok):
        assert qe.phrase_matches(actual, self.SLOT) is ok

    @pytest.mark.parametrize("actual,ok", [
        ("القطاع الحكومي", True), ("بالقطاع الحكومي", True), ("وبالقطاع الحكومي", True),
        ("قطاع حكومي", False), ("القطاع الحكومية", False), ("الحكومي", False), ("الجهات الحكومية", False),
        ("ببالقطاع الحكومي", False),
    ])
    def test_arabic_proclitic(self, actual, ok):
        assert qe.phrase_matches(actual, "القطاع الحكومي") is ok

    def test_slots_are_one_to_one_and_order_free(self):
        slots = [{"one_of": ["pharmaceutical sector", "the pharmaceutical sector"]}, "multinational organisations"]
        assert qe.slots_match(["multinational organisations", "the pharmaceutical sector"], slots)
        assert not qe.slots_match(["the pharmaceutical sector"], slots)
        assert not qe.slots_match(["pharmaceutical sector", "the pharmaceutical sector"], slots)
        assert not qe.slots_match(["pharmaceutical sector", "multinational organisations", "x"], slots)
        # a non-contiguous AND collapsed into one invented phrase is not accepted
        assert not qe.slots_match(["pharmaceutical sector in multinational organisations"], slots)


class TestParse:
    @pytest.mark.parametrize("raw,err", [
        (None, "no_response"), ("not json", "invalid_json"), ("[]", "not_an_object"),
        (json.dumps({"state": "none", "contexts": []}), "bad_keys"),
        (json.dumps({**qc("none"), "extra": 1}), "bad_keys"),
        (json.dumps({**qc("none"), "state": "partial"}), "bad_state"),
        (json.dumps({**qc("none"), "state": None}), "bad_state"),
        (json.dumps({**qc("identified", "x"), "source": "recruiter"}), "bad_source"),
        (json.dumps({**qc("identified"), "contexts": "banking sector"}), "bad_contexts"),
        (json.dumps(qc("identified", "")), "bad_contexts"),
        (json.dumps(qc("identified", "banking sector", "Banking  Sector")), "duplicate_contexts"),
        (json.dumps({"experience": {"minimum_years": 3, "relevant_roles": []}}), "missing_qualifying_context"),
    ])
    def test_rejects(self, raw, err):
        assert qe.parse_qc(raw) == (None, err)

    def test_accepts_bare_object_and_full_analysis(self):
        obj = qc("identified", "banking sector")
        assert qe.parse_qc(json.dumps(obj)) == (obj, None)
        assert qe.parse_qc(json.dumps({"experience": {"minimum_years": 3, "qualifying_context": obj}})) == (obj, None)


class TestChecks:
    def test_none_with_context_rejected(self):
        (r,), _ = scripted({"QC-D1": [qc("none", "Project Manager")]})
        assert r["state_ok"] and not r["consistency_ok"] and not r["pass"]

    def test_identified_without_context_rejected(self):
        (r,), s = scripted({"QC-A1": [qc("identified")]})
        assert r["state_ok"] and not r["consistency_ok"] and not r["pass"]
        assert s["overall"]["consistency_violations"] == 1 and s["overall"]["context_accuracy"] == 0.0

    def test_paraphrase_rejected(self):
        (r,), s = scripted({"QC-A1": [qc("identified", "banking industry")]})
        assert r["state_ok"] and not r["grounding_ok"] and r["context_ok"] is False and not r["pass"]
        assert r["ungrounded"] == ["banking industry"] and s["overall"]["grounding_failures"] == 1

    def test_arabic_paraphrase_rejected(self):
        (r,), _ = scripted({"QC-F1": [qc("identified", "البنوك")]})
        assert not r["grounding_ok"] and not r["pass"]

    def test_grounded_but_wrong_span_fails_context_only(self):
        (r,), s = scripted({"QC-A1": [qc("identified", "sector")]})
        assert r["state_ok"] and r["grounding_ok"] and r["context_ok"] is False and not r["pass"]
        assert s["overall"]["state_accuracy"] == 1.0 and s["overall"]["context_accuracy"] == 0.0

    def test_arabic_proclitic_answer_accepted(self):
        (r,), _ = scripted({"QC-F3": [qc("identified", "بالقطاع الحكومي")]})
        assert r["pass"]

    def test_generic_environment_as_context_is_false_qc_and_decoy(self):
        (r,), s = scripted({"QC-E1": [qc("identified", "fast-paced environment")]})
        assert r["grounding_ok"] and r["decoy_hit"] and not r["pass"]
        o = s["overall"]
        assert (o["false_qualifying_context"], o["false_qualifying_context_rate"], o["decoy_hits"]) == (1, 1.0, 1)
        assert o["false_qualifying_context_cases"] == ["QC-E1"]

    def test_about_us_sector_as_context_is_false_qc(self):
        (r,), s = scripted({"QC-M1": [qc("identified", "oil and gas contractor")]})
        assert r["decoy_hit"] and s["overall"]["false_qualifying_context"] == 1

    def test_uncertain_candidates_must_be_allowed_and_grounded(self):
        (ok, bad, ungrounded), s = scripted({"QC-J1": [qc("uncertain", "Big Four firm"),
                                                       qc("uncertain", "Auditor"),
                                                       qc("uncertain", "big audit firms")]}, runs=3)
        assert ok["pass"] and ok["candidates_ok"]
        assert bad["grounding_ok"] and bad["candidates_ok"] is False and not bad["pass"]
        assert not ungrounded["grounding_ok"] and not ungrounded["pass"]
        assert s["overall"]["candidates_violations"] == 2

    def test_role_specific_answered_as_identified(self):
        (r,), s = scripted({"QC-J1": [qc("identified", "banking sector", "Big Four firm")]})
        assert not r["state_ok"] and r["context_ok"] is None and not r["pass"]
        assert s["overall"]["identified_on_uncertain"] == 1 and s["overall"]["false_qualifying_context"] == 0

    def test_invalid_output_scored_as_wrong(self):
        (r,), s = scripted({"QC-A1": ["{oops"]})
        assert not r["valid"] and not r["pass"] and s["overall"]["invalid_outputs"] == 1


# ── metrics ─────────────────────────────────────────────────────────────────

class TestMetrics:
    def test_counts_and_rates(self):
        plan = {
            "QC-A1": [qc("identified", "banking sector")],                   # TP, pass
            "QC-A2": [qc("none")],                                           # missed
            "QC-A3": [qc("uncertain", "hospitality sector")],                # safe miss
            "QC-D1": [qc("identified", "Project Manager")],                  # false QC
            "QC-E2": [qc("none")],                                           # TN
            "QC-K1": [qc("uncertain", "regulated environments")],            # uncertain correct
            "QC-K2": [qc("none")],                                           # uncertain missed
            "QC-B1": [qc("identified", "oil and gas")],                      # TP state, wrong span
        }
        records, s = scripted(plan)
        o = s["overall"]
        assert o["runs"] == 8 and o["pass"] == 3 and o["pass_rate"] == 0.375
        assert o["state_accuracy"] == 0.5
        assert o["identified_precision"] == round(2 / 3, 4) and o["identified_recall"] == 0.5
        assert (o["false_qualifying_context"], o["false_qualifying_context_rate"]) == (1, 0.5)
        assert (o["missed_qualifying_context"], o["missed_qualifying_context_rate"]) == (1, 0.25)
        assert o["safe_miss_uncertain"] == 1 and o["uncertain_overuse"] == 1
        assert o["uncertain_accuracy"] == 0.5
        assert o["grounding_accuracy"] == 1.0 and o["grounding_failures"] == 0
        assert o["context_accuracy"] == 0.5
        assert s["by_family"]["A"]["runs"] == 3 and s["by_family"]["A"]["pass"] == 1
        assert set(s["by_split"]) == {"main"} and s["by_lang"]["ar"]["runs"] == 1
        assert sorted(s["failed"]) == ["QC-A2", "QC-A3", "QC-B1", "QC-D1", "QC-K2"]

    def test_stability(self):
        plan = {"QC-A1": [qc("identified", "banking sector"), qc("identified", "The Banking Sector"),
                          qc("identified", "the banking sector")],
                "QC-D1": [qc("none"), qc("identified", "Project Manager"), qc("none")]}
        _, s = scripted(plan, runs=3)
        st = s["overall"]["stability"]
        assert st["cases"] == 2 and st["stable_cases"] == 0 and st["unstable"] == ["QC-A1", "QC-D1"]
        assert st["modal_agreement"] == round((2 / 3 + 2 / 3) / 2, 4)
        _, s2 = scripted({"QC-A1": [qc("identified", "banking sector"), qc("identified", "Banking  sector")]}, runs=2)
        assert s2["overall"]["stability"]["stable_rate"] == 1.0          # same canonical answer

    def test_breakdowns_split_and_language(self):
        _, s = scripted({"QC-F1": [qc("none")], "QC-HO-A1": [qc("identified", "luxury cruise industry")]})
        assert s["by_split"]["main"]["pass"] == 0 and s["by_split"]["heldout"]["pass"] == 1
        assert s["by_lang"]["ar"]["missed_qualifying_context"] == 1 and s["by_lang"]["en"]["pass"] == 1

    def test_empty_denominators_are_none(self):
        _, s = scripted({"QC-D1": [qc("none")]})
        o = s["overall"]
        assert o["identified_precision"] is None and o["identified_recall"] is None
        assert o["grounding_accuracy"] is None and o["false_qualifying_context_rate"] == 0.0


# ── oracle ──────────────────────────────────────────────────────────────────

class TestOracle:
    @pytest.mark.parametrize("cases", [MAIN, HELDOUT], ids=["main", "heldout"])
    def test_oracle_is_perfect(self, cases):
        records = qe.run_all(cases, qe.OracleResponder(), runs=3)
        s = qe.summarize(records, cases)
        o = s["overall"]
        assert s["failed"] == [] and o["pass_rate"] == 1.0 and o["state_accuracy"] == 1.0
        assert o["false_qualifying_context"] == 0 and o["missed_qualifying_context"] == 0
        assert o["grounding_failures"] == 0 and o["grounding_accuracy"] == 1.0 and o["context_accuracy"] == 1.0
        assert o["identified_precision"] == 1.0 and o["identified_recall"] == 1.0 and o["uncertain_accuracy"] == 1.0
        assert o["stability"]["stable_rate"] == 1.0 and o["invalid_outputs"] == 0 and o["decoy_hits"] == 0
        assert all(m["pass_rate"] == 1.0 for g in ("by_family", "by_lang") for m in s[g].values())
        assert s["enumeration_gaps"]["confirmed"] == s["enumeration_gaps"]["flagged_cases"] != []


# ── isolation: no OpenAI, no production, real mode disabled ─────────────────

class TestIsolation:
    def test_static_imports(self):
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        top = {n.module for n in tree.body if isinstance(n, ast.ImportFrom)}
        top |= {a.name for n in tree.body if isinstance(n, ast.Import) for a in n.names}
        assert {m for m in top if m.startswith("services")} == {"services.s1_requirements.criteria",
                                                                 "services.s1_requirements.jd_text"}
        banned = ("openai", "ai_service", "llm", "database", "routers", "classifier", "sqlalchemy", "httpx",
                  "requests", "config")
        assert not {m for m in top if any(k in m for k in banned)}
        # the ONLY non-top-level imports are the lazy client imports inside make_openai_client
        lazy = {}
        for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
            for n in ast.walk(fn):
                if isinstance(n, ast.ImportFrom):
                    lazy.setdefault(fn.name, set()).add(n.module)
                elif isinstance(n, ast.Import):
                    lazy.setdefault(fn.name, set()).update(a.name for a in n.names)
        assert lazy == {"make_openai_client": {"openai", "config"}}

    @pytest.mark.parametrize("mode", ["oracle", "replay"])
    def test_no_openai_at_runtime(self, mode, tmp_path):
        replay = tmp_path / "replay.json"
        replay.write_text(json.dumps({"responses": {"QC-A1": [qc("identified", "banking sector")]}}), encoding="utf-8")
        args = ["--mode", mode, "--set", "main", "--out", str(tmp_path / "out")]
        if mode == "replay":
            args += ["--replay", str(replay), "--cases", "QC-A1"]
        code = (f"import runpy, sys; sys.argv = {[str(SCRIPT)] + args!r}\n"
                "try:\n    runpy.run_path(sys.argv[0], run_name='__main__')\nexcept SystemExit as e:\n    rc = e.code\n"
                "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('openai', 'sqlalchemy', 'asyncpg', 'httpx')"
                " or m in ('services.ai_service', 'services.s0_experience.llm_call', 'config'))\n"
                "print('RC', rc, 'BAD', bad)")
        env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(BACKEND)}            # no OPENAI_API_KEY
        out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True,
                             timeout=120)
        assert out.stdout.strip().splitlines()[-1] == "RC 0 BAD []", out.stdout + out.stderr
        res = json.loads((tmp_path / "out" / "results.json").read_text(encoding="utf-8"))
        assert res["summary"]["failed"] == [] and res["meta"]["mode"] == mode

    def test_replay_scores_recorded_answers(self, tmp_path):
        replay = tmp_path / "r.json"
        replay.write_text(json.dumps({"responses": {
            "QC-A1": [json.dumps(qc("identified", "banking sector")), qc("none")],
            "QC-D1": [qc("none")]}}, ensure_ascii=False), encoding="utf-8")
        out = tmp_path / "o"
        assert qe.main(["--mode", "replay", "--replay", str(replay), "--cases", "QC-A1,QC-D1", "--set", "main",
                        "--out", str(out)]) == 0
        res = json.loads((out / "results.json").read_text(encoding="utf-8"))
        errs = [(r["case_id"], r["run"], r["error"]) for r in res["records"]]
        assert errs == [("QC-A1", 0, None), ("QC-A1", 1, None), ("QC-D1", 0, None), ("QC-D1", 1, "no_response")]
        assert res["summary"]["overall"]["missed_qualifying_context"] == 1
        assert (out / "report.md").read_text(encoding="utf-8").startswith("# Qualifying-context evaluation (replay")

    def test_real_mode_without_confirmation_makes_no_call(self, capsys, monkeypatch):
        def boom():
            raise AssertionError("client must not be built without --confirm-real")
        monkeypatch.setattr(qe, "make_openai_client", boom)
        assert qe.main(["--mode", "real", "--set", "main", "--runs", "5"]) == 2
        out, err = capsys.readouterr()
        assert "no call made" in err and json.loads(out[:out.rindex("}") + 1])["calls"] == 34 * 5

    def test_cli_oracle_exit_code(self, tmp_path):
        assert qe.main(["--mode", "oracle", "--set", "all", "--runs", "2", "--out", str(tmp_path)]) == 0


# ── prompt guard for future eval-only candidate templates ───────────────────

class TestPromptGuard:
    def test_existing_candidate_templates_leak_nothing(self):
        templates = sorted(qe.PROMPT_DIR.glob("*.txt")) if qe.PROMPT_DIR.exists() else []
        for t in templates:
            assert qe.prompt_leaks(t.read_text(encoding="utf-8"), ALL) == [], t.name

    def test_guard_detects_fixture_phrases(self):
        prompt = ("Return experience.qualifying_context.\nExample: '... as a Sommelier in the luxury cruise industry' "
                  "-> identified.\nGeneric wording such as في بيئة عمل محفزة ومرنة is none.")
        assert qe.prompt_leaks(prompt, HELDOUT) == sorted(
            ["Sommelier", "luxury cruise industry", "the luxury cruise industry", "in the luxury cruise industry",
             "في بيئة عمل محفزة ومرنة"])
        assert qe.prompt_leaks("Identify sector, project-domain or organisation restrictions verbatim.", ALL) == []

    def test_no_production_prompt_contains_fixture_phrases(self):
        src = (BACKEND / "services" / "ai_service.py").read_text(encoding="utf-8")
        assert "qualifying_context" not in src


# ── candidate_qc-1: eval-only prompt, leakage, contract, prepared request, gates ──

import hashlib  # noqa: E402

PROMPT_FILE = qe.PROMPT_DIR / "candidate_qc-1.txt"
# byte-for-byte pins taken from commit f69328d (fixtures) and the production analysis module at that commit
PINNED_SHA256 = {
    BACKEND / "scripts" / "qc_eval_fixtures" / "qc_main_cases.json":
        "c5fcd5024d9673217d0b1e8b8413d4628025932b86fcb97f56b96fc57f98317c",
    BACKEND / "scripts" / "qc_eval_fixtures" / "qc_heldout_cases.json":
        "d94a39f343e0185cf9d0cca664d1c744f52d5b66b6b32ac57edee22ed3a1b91f",
    BACKEND / "services" / "ai_service.py":
        "b1c4ba7b69d8a96060d7c3f345823bafd77602917cb21486f4b204f3e91f669c",
    # the evaluated candidate prompt, as committed in 656246a
    BACKEND / "scripts" / "qc_eval_fixtures" / "prompts" / "candidate_qc-1.txt":
        "fc979dd4a48b2aa5922c87f62e613700f898da13599e43e62a4fbc1304c851df",
}
CONTRACT_LINE = '{"state": "identified" | "none" | "uncertain", "contexts": ["..."], "source": "analysis"}'
# "experience" is the subject word of every requirement line; it reaches the fixture vocabulary only through the
# S1 role "User Experience Designer", so the EXAMPLE content-word check allowlists it explicitly
EXAMPLE_GENERIC = GENERIC | {"experience"}


def prompt_text() -> str:
    return PROMPT_FILE.read_text(encoding="utf-8")


def prompt_examples(text: str) -> list[dict]:
    """Example blocks: 'Example N' / one or more 'JD: ' lines / one 'Answer: ' JSON line."""
    out, cur = [], None
    for ln in text.splitlines():
        if ln.startswith("Example "):
            cur = {"name": ln.strip(), "jd": [], "answer": None}
            out.append(cur)
        elif cur is not None and ln.startswith("JD: "):
            cur["jd"].append(ln[4:])
        elif cur is not None and ln.startswith("Answer: "):
            cur["answer"] = ln[8:]
            cur = None
    return out


def _has_arabic(s: str) -> bool:
    return any("؀" <= ch <= "ۿ" for ch in s)


class TestCandidatePrompt:
    def test_exists_and_loads_with_exact_sha(self):
        assert PROMPT_FILE.is_file()
        p = qe.load_prompt("candidate_qc-1")
        assert p["version"] == "candidate_qc-1" and p["text"] == prompt_text()
        assert p["sha256"] == hashlib.sha256(PROMPT_FILE.read_bytes()).hexdigest()
        assert p["user_template_sha256"] == hashlib.sha256(qe.USER_TEMPLATE.encode()).hexdigest()

    def test_no_fixture_phrase_leaks(self):
        text = prompt_text()
        assert qe.prompt_leaks(text, MAIN) == [] and qe.prompt_leaks(text, HELDOUT) == []
        assert _phrase_overlaps({text}, s1_phrases()) == []

    def test_example_vocabulary_shares_no_content_word_with_any_fixture(self):
        exs = prompt_examples(prompt_text())
        ex_words = {w for e in exs for ln in e["jd"] + json.loads(e["answer"])["contexts"] for w in qe.words(ln)}
        fixture = {w for p in qe.fixture_phrases(MAIN) | qe.fixture_phrases(HELDOUT) | s1_phrases()
                   for w in qe.words(p)}
        assert ex_words & fixture - EXAMPLE_GENERIC == set()

    def test_no_fixture_jd_line_or_answer_in_prompt(self):
        pw = qe.words(prompt_text())
        for c in ALL:
            for ln in c["jd_lines"]:
                core = qe.words(ln.lstrip("- "))
                if len(core) >= 4:
                    assert not qe._contains_run(pw, core), (c["id"], ln)

    def test_exact_three_key_contract(self):
        text = prompt_text()
        assert CONTRACT_LINE in text
        assert "exactly these three keys" in text and "No markdown, no explanation, no other keys." in text
        for rule in ('"identified" -> contexts lists one or more phrases', '"none" -> contexts is []',
                     '"source" is always "analysis"'):
            assert rule in text

    def test_examples_are_bilingual_and_cover_every_state(self):
        exs = prompt_examples(prompt_text())
        assert len(exs) >= 12 and all(e["jd"] and e["answer"] for e in exs)
        ar = [e for e in exs if _has_arabic(" ".join(e["jd"]))]
        en = [e for e in exs if not _has_arabic(" ".join(e["jd"]))]
        assert len(ar) >= 3 and len(en) >= 6
        assert {json.loads(e["answer"])["state"] for e in exs} == set(qe.STATES)
        assert {json.loads(e["answer"])["state"] for e in ar} >= {"identified", "none"}

    def test_examples_obey_the_harness_contract(self):
        for e in prompt_examples(prompt_text()):
            parsed, err = qe.parse_qc(e["answer"])
            assert err is None, (e["name"], err)
            jd = qe.JDText("\n".join(e["jd"]))
            assert all(qe.grounded(jd, c) for c in parsed["contexts"]), e["name"]
            n = len(parsed["contexts"])
            assert {"identified": n >= 1, "none": n == 0, "uncertain": True}[parsed["state"]], e["name"]
            if _has_arabic(" ".join(e["jd"])):
                assert all(_has_arabic(c) for c in parsed["contexts"]), e["name"]

    def test_design_rules_present(self):
        text = prompt_text()
        for marker in ("ATTACHMENT", "RESTRICTION", "CHECKABILITY", "WHERE NOT TO LOOK", "about us",
                       "only part of the requirement", "preferably / ideally", "is preferred / an advantage",
                       "combined with AND", "ONE context", "character-for-character", "Never translate"):
            assert marker in text, marker


class TestPreparedRequest:
    def test_payload_matches_approved_config(self):
        prompt = qe.load_prompt()
        payload = qe.request_payload(BY_ID["QC-A1"], prompt)
        assert set(payload) == {"model", "messages", "temperature", "max_tokens", "response_format"}
        assert (payload["model"], payload["temperature"], payload["max_tokens"]) == ("gpt-4o-mini", 0.2, 200)
        assert payload["response_format"] == {"type": "json_object"} and qe.REAL_CONFIG["runs"] == 5
        assert payload["messages"][0] == {"role": "system", "content": prompt_text()}

    @pytest.mark.parametrize("case", ALL, ids=lambda c: c["id"])
    def test_model_sees_the_jd_only(self, case):
        prompt = qe.load_prompt()
        user = qe.request_payload(case, prompt)["messages"][1]
        assert user == {"role": "user", "content": qe.USER_TEMPLATE.replace("{jd}", "\n".join(case["jd_lines"]))}
        poisoned = {**case, "family": "ZZ", "description": "LEAK", "analysis_stub": {"relevant_roles": ["LEAK"]},
                    "expected": {"state": "LEAK"}, "oracle": {"state": "LEAK"}, "decoys": ["LEAK"], "flags": ["LEAK"]}
        assert qe.request_payload(poisoned, prompt) == qe.request_payload(case, prompt)
        assert "LEAK" not in json.dumps(qe.request_payload(poisoned, prompt))

    def test_braces_in_jd_are_not_template_fields(self):
        case = {**BY_ID["QC-D1"], "jd_lines": ["- 3 years {jd} {x}"]}
        assert qe.render_messages(case, "S")[1]["content"].endswith("<<<JD\n- 3 years {jd} {x}\nJD>>>")

    def test_replay_meta_records_prompt_model_and_fixture(self, tmp_path):
        replay = tmp_path / "r.json"
        replay.write_text(json.dumps({"responses": {"QC-A1": [qc("identified", "banking sector")] * 5}}),
                          encoding="utf-8")
        assert qe.main(["--mode", "replay", "--replay", str(replay), "--cases", "QC-A1", "--set", "main",
                        "--out", str(tmp_path / "o")]) == 0
        meta = json.loads((tmp_path / "o" / "results.json").read_text(encoding="utf-8"))["meta"]
        assert meta["prompt_version"] == "candidate_qc-1"
        assert meta["prompt_sha256"] == hashlib.sha256(PROMPT_FILE.read_bytes()).hexdigest()
        assert (meta["model"], meta["temperature"], meta["max_tokens"], meta["runs"]) == ("gpt-4o-mini", 0.2, 200, 5)
        assert meta["response_format"] == {"type": "json_object"} and meta["fixture_versions"] == ["qc-main-1"]

    def test_oracle_meta_claims_no_prompt_or_model(self, tmp_path):
        assert qe.main(["--mode", "oracle", "--set", "main", "--out", str(tmp_path)]) == 0
        res = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
        assert res["meta"]["prompt_version"] is None and res["meta"]["model"] is None
        assert res["summary"]["gates"]["status"] == "not_applicable"

    def test_real_mode_refuses_heldout_without_explicit_permission(self, capsys, monkeypatch):
        monkeypatch.setattr(qe, "make_openai_client", lambda: (_ for _ in ()).throw(AssertionError("no client")))
        for argv in (["--set", "heldout"], ["--set", "all"]):
            assert qe.main(["--mode", "real", "--runs", "5", "--confirm-real", *argv]) == 2
            assert "--allow-heldout" in capsys.readouterr().err

    def test_real_mode_refuses_non_approved_configuration(self):
        for extra in (["--model", "gpt-4o"], ["--temperature", "0"], ["--max-tokens", "500"]):
            with pytest.raises(SystemExit):
                qe.main(["--mode", "real", "--set", "main", "--confirm-real", *extra])


def gate_records(plan: dict, cases, runs: int = 5):
    return qe.run_all(cases, qe.ScriptedResponder(plan), runs=runs)


def _gate(g, split, grp, name):
    return g["splits"][split][grp][name]


class TestGates:
    def test_oracle_never_claims_a_verdict(self):
        g = qe.evaluate_gates(qe.run_all(MAIN, qe.OracleResponder(), runs=5), "oracle", 5)
        assert g["status"] == "not_applicable" and "verdict" not in g

    def test_perfect_model_outputs_pass_every_gate(self):
        g = qe.evaluate_gates(gate_records({}, ALL), "replay", 5)
        assert g["verdict"] == "pass" and "note" not in g
        assert set(g["splits"]) == {"main", "heldout"} and g["cross"]["main_heldout_state_gap"]["value"] == 0.0
        assert set(g["splits"]["main"]["split"]) == {n for n, _, _ in qe.SPLIT_GATES["main"]}
        assert set(g["splits"]["heldout"]["hard"]) == {n for n, _, _ in qe.HARD_GATES}

    def test_single_false_qualifying_context_fails_hard_gate(self):
        plan = {"QC-E1": [qc("none")] * 4 + [qc("identified", "fast-paced environment")]}
        g = qe.evaluate_gates(gate_records(plan, MAIN), "replay", 5)
        r = _gate(g, "main", "hard", "false_qualifying_context")
        assert (r["value"], r["pass"], g["verdict"]) == (1, False, "fail")

    def test_arabic_false_qualifying_context_gate(self):
        plan = {"QC-HO-G1": [qc("identified", "بيئة عمل محفزة ومرنة")]}
        g = qe.evaluate_gates(gate_records(plan, HELDOUT, runs=1), "replay", 1)
        assert _gate(g, "heldout", "hard", "arabic_false_qualifying_context") == {"value": 1, "op": "==",
                                                                                 "threshold": 0, "pass": False}
        assert "provisional" in g["note"]

    def test_role_specific_identified_fails_hard_gate(self):
        plan = {"QC-J1": [qc("identified", "banking sector", "Big Four firm")]}
        g = qe.evaluate_gates(gate_records(plan, MAIN), "replay", 5)
        assert _gate(g, "main", "hard", "role_specific_identified") == {"value": 5, "op": "==", "threshold": 0,
                                                                        "pass": False}
        assert _gate(g, "main", "hard", "false_qualifying_context")["value"] == 0

    def test_grounding_and_invalid_output_gates(self):
        plan = {"QC-A1": [qc("identified", "banking industry")], "QC-A2": ["{oops"]}
        g = qe.evaluate_gates(gate_records(plan, MAIN), "replay", 5)
        assert _gate(g, "main", "hard", "grounding_failures")["value"] == 5
        assert _gate(g, "main", "hard", "invalid_outputs")["value"] == 5

    def test_case_never_identified_is_flagged(self):
        plan = {"QC-B3": [qc("uncertain", "railway projects")]}
        g = qe.evaluate_gates(gate_records(plan, MAIN), "replay", 5)
        r = _gate(g, "main", "split", "identified_cases_never_identified")
        assert (r["value"], r["pass"]) == (1, False) and g["splits"]["main"]["never_identified_cases"] == ["QC-B3"]
        assert _gate(g, "main", "split", "safe_miss_uncertain_rate")["value"] == round(5 / 95, 4)
        assert _gate(g, "main", "hard", "false_qualifying_context")["pass"] is True

    def test_rate_gates_and_thresholds(self):
        # 2 of 95 expected-identified main runs missed as NONE (2.1%) passes; 6 (6.3%) fails the 5% gate
        ok = {"QC-A1": [qc("none")] + [qc("identified", "banking sector")] * 4,
              "QC-A2": [qc("none")] + [qc("identified", "telecommunications industry")] * 4}
        g = qe.evaluate_gates(gate_records(ok, MAIN), "replay", 5)
        r = _gate(g, "main", "split", "missed_qualifying_context_rate")
        assert (r["value"], r["pass"]) == (round(2 / 95, 4), True)
        assert _gate(g, "main", "split", "case_stability")["value"] == round(32 / 34, 4)
        bad = {cid: [qc("none")] * 2 + [qc("identified", ctx)] * 3
               for cid, ctx in (("QC-A1", "banking sector"), ("QC-A2", "telecommunications industry"),
                                ("QC-A3", "hospitality sector"))}
        g2 = qe.evaluate_gates(gate_records(bad, MAIN), "replay", 5)
        r2 = _gate(g2, "main", "split", "missed_qualifying_context_rate")
        assert (r2["value"], r2["pass"]) == (round(6 / 95, 4), False)
        assert _gate(g2, "main", "split", "case_stability") == {"value": round(31 / 34, 4), "op": ">=",
                                                                 "threshold": 0.9, "pass": True}
        assert g2["verdict"] == "fail"
        bad["QC-B1"] = [qc("none")] + [qc("identified", "oil and gas projects")] * 4
        g3 = qe.evaluate_gates(gate_records(bad, MAIN), "replay", 5)
        assert _gate(g3, "main", "split", "case_stability") == {"value": round(30 / 34, 4), "op": ">=",
                                                                 "threshold": 0.9, "pass": False}

    def test_arabic_english_gap_gate(self):
        plan = {cid: [qc("none")] for cid in ("QC-F1", "QC-F2", "QC-F3")}
        g = qe.evaluate_gates(gate_records(plan, MAIN), "replay", 5)
        r = _gate(g, "main", "split", "arabic_english_state_gap")
        assert r["value"] == round(1 - 6 / 9, 4) and r["pass"] is False

    def test_main_heldout_gap_gate(self):
        plan = {c["id"]: [qc("none")] for c in HELDOUT if c["expected"]["state"] == "identified"}
        g = qe.evaluate_gates(gate_records(plan, ALL), "replay", 5)
        assert g["cross"]["main_heldout_state_gap"]["pass"] is False
        assert _gate(g, "heldout", "split", "identified_recall")["pass"] is False

    def test_empty_denominator_is_not_a_pass(self):
        g = qe.evaluate_gates(gate_records({}, [BY_ID["QC-D1"]]), "replay", 5)
        assert _gate(g, "main", "split", "identified_precision")["pass"] is None
        assert g["verdict"] == "incomplete"

    def test_report_renders_gates(self):
        recs = gate_records({}, MAIN)
        s = qe.summarize(recs, MAIN)
        s["gates"] = qe.evaluate_gates(recs, "replay", 5)
        md = qe.render_markdown({"mode": "replay", "eval_version": qe.EVAL_VERSION, "fixture_versions": ["qc-main-1"],
                                 "runs": 5, "prompt_version": "candidate_qc-1", "prompt_sha256": "x",
                                 "model": "gpt-4o-mini", "temperature": 0.2}, s)
        assert "verdict: **pass**" in md and "[main/hard] false_qualifying_context: 0 == 0 -> PASS" in md


class TestPinnedBytes:
    @pytest.mark.parametrize("path", list(PINNED_SHA256), ids=lambda p: p.name)
    def test_unchanged_since_f69328d(self, path):
        assert hashlib.sha256(path.read_bytes()).hexdigest() == PINNED_SHA256[path]

    def test_production_has_no_qualifying_context_and_no_candidate_reference(self):
        src = (BACKEND / "services" / "ai_service.py").read_text(encoding="utf-8")
        assert "qualifying_context" not in src and "candidate_qc" not in src
        service = BACKEND / "services" / "qualifying_context"
        for path in (BACKEND / "services").rglob("*.py"):
            if service in path.parents:
                continue          # the validated production service records its prompt version by design
            text = path.read_text(encoding="utf-8")
            assert "candidate_qc" not in text and "qc_context_eval" not in text, path
        # phase P1: the service exists but nothing in production imports it (not wired)
        for sub in ("services", "workers", "routers"):
            for path in (BACKEND / sub).rglob("*.py"):
                if service in path.parents:
                    continue
                assert "qualifying_context" not in path.read_text(encoding="utf-8"), path


# ── real mode (fake client only: no network, no OpenAI import) ─────────────

class _Usage:
    prompt_tokens, completion_tokens, total_tokens = 2100, 25, 2125


class _Resp:
    def __init__(self, content, model="gpt-4o-mini-2024-07-18", finish="stop"):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})(), "finish_reason": finish})()]
        self.model, self.usage = model, _Usage()


class FakeClient:
    """Records every request; answers from a per-case script (default: the oracle answer) or raises."""

    def __init__(self, script=None):
        self.calls, self.script, self._n = [], script or {}, Counter()
        self.chat = type("Chat", (), {"completions": self})()

    def create(self, **kwargs):
        self.calls.append(kwargs)
        jd = kwargs["messages"][1]["content"]
        case = next(c for c in ALL if qe.USER_TEMPLATE.replace("{jd}", qe.case_jd(c)) == jd)
        k = self._n[case["id"]]
        self._n[case["id"]] += 1
        seq = self.script.get(case["id"])
        item = seq[min(k, len(seq) - 1)] if seq else qe.oracle_response(case)
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, _Resp) else _Resp(item if isinstance(item, str) else json.dumps(item))


def real_run(cases, script=None, runs=5):
    client = FakeClient(script)
    prompt = qe.load_prompt()
    meta = {"prompt_version": prompt["version"], "prompt_sha256": prompt["sha256"], "model": "gpt-4o-mini",
            "temperature": 0.2}
    records = qe.run_all(cases, qe.make_real_responder(prompt, client=client), runs=runs, run_meta=meta)
    return client, records


SENTINEL = "ZQXSENTINEL"


def poisoned(case):
    return {**case, "id": SENTINEL + "ID", "family": SENTINEL + "FAM", "description": SENTINEL + "DESC",
            "analysis_stub": {"minimum_years": 99, "relevant_roles": [SENTINEL + "ROLE"]},
            "expected": {**case["expected"], "note": SENTINEL + "EXP"}, "decoys": [SENTINEL + "DECOY"],
            "oracle": {**case["oracle"], "note": SENTINEL + "ORACLE"}, "flags": [SENTINEL + "FLAG"]}


class TestRealMode:
    def test_request_is_exactly_prompt_plus_jd(self):
        client, _ = real_run(MAIN, runs=1)
        system = prompt_text()
        assert len(client.calls) == len(MAIN)
        for case, call in zip(MAIN, client.calls):
            assert call == {"model": "gpt-4o-mini", "temperature": 0.2, "max_tokens": 200,
                            "response_format": {"type": "json_object"},
                            "messages": [{"role": "system", "content": system},
                                         {"role": "user",
                                          "content": qe.USER_TEMPLATE.replace("{jd}", "\n".join(case["jd_lines"]))}]}

    @pytest.mark.parametrize("case", ALL, ids=lambda c: c["id"])
    def test_no_fixture_information_reaches_the_model(self, case):
        client = FakeClient()
        responder = qe.make_real_responder(qe.load_prompt(), client=client)
        responder.respond(poisoned(case), 0)
        sent = json.dumps(client.calls[0], ensure_ascii=False)
        assert SENTINEL not in sent
        assert case["id"] not in sent and case["description"] not in sent
        user = client.calls[0]["messages"][1]["content"]
        assert user == qe.USER_TEMPLATE.replace("{jd}", "\n".join(case["jd_lines"]))

    def test_oracle_and_expected_contexts_are_never_added(self):
        # a JD-only case whose labels name phrases that are NOT in its JD: they must not appear in the request
        case = {**BY_ID["QC-D1"], "expected": {"state": "identified", "contexts": ["lunar mining outposts"]},
                "oracle": {"state": "identified", "contexts": ["lunar mining outposts"], "source": "analysis"}}
        client = FakeClient()
        qe.make_real_responder(qe.load_prompt(), client=client).respond(case, 0)
        assert "lunar" not in json.dumps(client.calls[0])

    def test_records_carry_full_diagnostics(self):
        _, records = real_run([BY_ID["QC-A1"]], runs=1)
        (r,) = records
        assert r["case_id"] == "QC-A1" and r["run"] == 0 and r["fixture_version"] == "qc-main-1"
        assert r["prompt_version"] == "candidate_qc-1"
        assert r["prompt_sha256"] == hashlib.sha256(PROMPT_FILE.read_bytes()).hexdigest()
        assert (r["model"], r["temperature"]) == ("gpt-4o-mini", 0.2)
        assert r["raw"] == qe.oracle_response(BY_ID["QC-A1"]) and r["parsed"] == BY_ID["QC-A1"]["oracle"]
        assert r["error"] is None and r["technical_error"] is None and r["finish_reason"] == "stop"
        assert r["usage"] == {"prompt_tokens": 2100, "completion_tokens": 25, "total_tokens": 2125}
        assert r["response_model"] == "gpt-4o-mini-2024-07-18"
        assert r["expected"] == BY_ID["QC-A1"]["expected"] and r["expected_state"] == "identified"
        assert r["pass"] and r["state_ok"] and r["grounding_ok"] and r["context_ok"]

    def test_technical_failure_is_explicit_not_an_answer(self):
        script = {"QC-A1": [RuntimeError("upstream 503"), qe.oracle_response(BY_ID["QC-A1"])]}
        client, records = real_run([BY_ID["QC-A1"], BY_ID["QC-D1"]], script=script, runs=2)
        assert len(client.calls) == 4                              # exactly one call per run: no retry
        fail = records[0]
        assert fail["error"] == qe.FAILED_TECHNICAL and fail["technical_error"] == "RuntimeError: upstream 503"
        assert fail["raw"] is None and fail["parsed"] is None and fail["state"] is None and not fail["pass"]
        s = qe.summarize(records, [BY_ID["QC-A1"], BY_ID["QC-D1"]])
        o = s["overall"]
        assert o["failed_technical"] == 1 and o["runs"] == 3 and o["state_accuracy"] == 1.0
        assert o["missed_qualifying_context"] == 0 and o["invalid_outputs"] == 0
        assert s["technical_failures"] == [{"case_id": "QC-A1", "run": 0, "error": "RuntimeError: upstream 503"}]
        g = qe.evaluate_gates(records, "real", 2)
        assert g["failed_technical"] == 1 and g["verdict"] in ("incomplete", "fail") and g["verdict"] != "pass"

    def test_gates_never_pass_with_technical_failures(self):
        script = {"QC-A1": [qe.oracle_response(BY_ID["QC-A1"])] * 4 + [TimeoutError("timed out")]}
        _, records = real_run(MAIN, script=script, runs=5)
        g = qe.evaluate_gates(records, "real", 5)
        assert g["failed_technical"] == 1 and g["verdict"] == "incomplete"
        _, clean = real_run(MAIN, runs=5)
        assert qe.evaluate_gates(clean, "real", 5)["verdict"] == "pass"

    def test_no_fallback_model(self):
        script = {"QC-A1": [RuntimeError("model overloaded")] * 5}
        client, records = real_run([BY_ID["QC-A1"]], script=script, runs=5)
        assert {c["model"] for c in client.calls} == {"gpt-4o-mini"} and len(client.calls) == 5
        assert all(r["error"] == qe.FAILED_TECHNICAL for r in records)
        src = SCRIPT.read_text(encoding="utf-8")
        assert "fallback_model" not in src and src.count('"gpt-4o-mini"') == 1     # only REAL_CONFIG names it

    def test_client_has_no_sdk_retries(self, monkeypatch):
        created = {}

        class FakeOpenAI:
            def __init__(self, **kw):
                created.update(kw)
        fake_mod = type(sys)("openai")
        fake_mod.OpenAI = FakeOpenAI
        fake_cfg = type(sys)("config")
        fake_cfg.get_settings = lambda: type("S", (), {"openai_api_key": "sk-test"})()
        monkeypatch.setitem(sys.modules, "openai", fake_mod)
        monkeypatch.setitem(sys.modules, "config", fake_cfg)
        qe.make_openai_client()
        assert created == {"api_key": "sk-test", "max_retries": 0, "timeout": qe.REAL_CLIENT_TIMEOUT_S}

    @pytest.mark.parametrize("content,err", [("{oops", "invalid_json"),
                                             ('{"state": "identified", "contexts": ["x"]}', "bad_keys"),
                                             ('{"state": "maybe", "contexts": [], "source": "analysis"}',
                                              "bad_state"),
                                             ('```json\n{"state": "none", "contexts": [], "source": "analysis"}\n```',
                                              "invalid_json"),
                                             ("", "invalid_json")])
    def test_malformed_output_stays_invalid(self, content, err):
        _, (r,) = real_run([BY_ID["QC-D1"]], script={"QC-D1": [content]}, runs=1)
        assert r["error"] == err and not r["valid"] and r["state"] is None and not r["pass"]
        assert r["raw"] == content and r["technical_error"] is None

    def test_truncated_output_is_invalid_and_flagged(self):
        resp = _Resp('{"state": "identified", "contexts": ["banking', finish="length")
        _, (r,) = real_run([BY_ID["QC-A1"]], script={"QC-A1": [resp]}, runs=1)
        assert r["error"] == "invalid_json" and r["finish_reason"] == "length" and not r["pass"]

    def test_confirmed_cli_run_uses_injected_client_and_writes_meta(self, tmp_path, monkeypatch):
        client = FakeClient()
        monkeypatch.setattr(qe, "make_openai_client", lambda: client)
        assert qe.main(["--mode", "real", "--set", "main", "--cases", "QC-A1,QC-E1", "--runs", "5",
                        "--confirm-real", "--out", str(tmp_path)]) == 0
        res = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
        assert len(client.calls) == 10 and len(res["records"]) == 10
        m = res["meta"]
        assert (m["mode"], m["model"], m["temperature"], m["max_tokens"], m["runs"]) == (
            "real", "gpt-4o-mini", 0.2, 200, 5)
        assert m["prompt_version"] == "candidate_qc-1" and m["fixture_versions"] == ["qc-main-1"]
        assert m["prompt_sha256"] == hashlib.sha256(PROMPT_FILE.read_bytes()).hexdigest()
        assert res["summary"]["gates"]["status"] == "evaluated"

    def test_plan_counts_and_cost_bound(self):
        plan = qe.real_plan(MAIN, qe.load_prompt(), 5)
        assert plan["calls"] == 170 and plan["output_token_upper_bound"] == 170 * 200
        assert 0 < plan["cost_upper_bound_usd"] < 1.0
