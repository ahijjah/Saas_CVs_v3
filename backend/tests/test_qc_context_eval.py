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
                 "oracle", "split"}

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
        mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert {m for m in mods if m.startswith("services")} == {"services.s1_requirements.criteria",
                                                                  "services.s1_requirements.jd_text"}
        assert not {m for m in mods if any(k in m for k in ("openai", "ai_service", "llm", "database", "routers",
                                                             "classifier", "sqlalchemy", "httpx", "requests"))}

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

    def test_real_mode_disabled(self, capsys):
        assert qe.main(["--mode", "real"]) == 2
        assert "DISABLED" in capsys.readouterr().err
        with pytest.raises(NotImplementedError):
            qe.make_real_responder()

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
