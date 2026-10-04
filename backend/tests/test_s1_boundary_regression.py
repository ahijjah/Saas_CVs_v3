"""Offline tests for the S1 boundary-regression harness (no API, no database)."""
import ast
import asyncio
import importlib.util
import json
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("s1_boundary_regression",
                                               BACKEND / "scripts" / "s1_boundary_regression.py")
br = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(br)

CASES = br.load_cases()
BY_ID = {c["id"]: c for c in CASES}


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def scripted_records(plan: dict, runs: int = 1, cases=None):
    """plan: case id -> list of client items per run (default: the oracle answer)."""
    cases = cases or [BY_ID[c] for c in plan]
    counters = {}

    def client_for(case):
        k = counters.get(case["id"], 0)
        counters[case["id"]] = k + 1
        items = plan.get(case["id"])
        items = items[k] if items else [br.oracle_response(case)]
        return br.ScriptedClient(*items)
    return run(br.run_all(cases, runs=runs, client_for=client_for, model="gpt-4o-mini")), cases


def with_oracle(case_id, **override) -> str:
    d = json.loads(br.oracle_response(BY_ID[case_id]))
    d["criteria"][0].update(override)
    return json.dumps(d, ensure_ascii=False)


# ── fixture validity ────────────────────────────────────────────────────────

class TestFixture:
    def test_size_families_and_unique_ids(self):
        assert len(CASES) >= 30 and len(BY_ID) == len(CASES)
        fams = {c["family"][0] for c in CASES}
        assert fams == set("ABCDEFGHIJKLMNOPQRST")

    def test_arabic_coverage(self):
        arabic = [c for c in CASES if any(re.search(r"[؀-ۿ]", ln) for ln in c["jd_lines"])]
        assert len(arabic) >= 5
        assert any(c["family"].startswith("B") and c["analysis"]["relevant_roles"] == ["procurement"]
                   for c in arabic)                                         # translated function
        assert any(m and m["text"].startswith("ك") for c in arabic
                   for m in (t.get("jd_span") for t in c["oracle"]["targets"]))   # attached letter
        assert any(c["expected"]["targets"] and c["expected"]["targets"][0]["provenance"] == "original_ai"
                   for c in arabic)                                         # negative qualifier case
        assert any(not re.match(r"^[\x00-\x7f]*$", c["expected"]["duration"] or "x") for c in arabic)

    def test_labels_are_complete(self):
        for c in CASES:
            e = c["expected"]
            for k in ("policy", "targets", "setting", "duration", "ambiguity", "status"):
                assert k in e, (c["id"], k)
            hints = c["analysis"]["relevant_roles"]
            assert [t["text"] for t in e["targets"] if "mapped" in t] == hints, c["id"]
            assert e["status"] in ("resolved", "needs_confirmation") or "one_of" in e["status"]
            assert "Requirements" in c["jd_lines"] or "المتطلبات" in c["jd_lines"] or c["family"] == "S_conflict"

    def test_positive_and_negative_mapping_controls(self):
        mapped = [t["mapped"] for c in CASES for t in c["expected"]["targets"] if "mapped" in t]
        assert sum(mapped) >= 5 and mapped.count(False) >= 15                  # trust-bearing positives
        # s1-5.2: equivalences through a form pair are labelled unverified candidates, not positives
        candidates = [c["id"] for c in CASES if "equivalence_unverified" in c["expected"].get("reasons_include", [])]
        assert candidates == ["G1", "G2"]

    def test_synthetic_only_no_pii(self):
        text = br.FIXTURE.read_text(encoding="utf-8")
        assert not re.search(r"[\w.+-]+@[\w-]+\.\w+", text)                 # no e-mail addresses
        assert not re.search(r"\+?\d[\d\s-]{8,}\d", text)                   # no phone numbers
        assert "candidate" not in text.lower() or "No candidate" in text

    def test_oracle_answers_score_100_percent(self):
        """Labels agree with the deterministic validator/assembler for every case."""
        records, cases = scripted_records({}, cases=CASES)
        assert all(r["outcome"] == "ok" and r["pass"] for r in records), [
            (r["case"], r.get("checks"), r["validation"]) for r in records if not r.get("pass")]
        s = br.summarize(records, cases)
        assert s["jd_asserted"]["fp"] == s["jd_asserted"]["fn"] == 0
        assert s["policy_accuracy"] == s["status_accuracy"] == 1.0

    def test_alternative_expectation(self):
        alt = with_oracle("R1", duration=None, ambiguity=["multiple_durations"])
        records, _ = scripted_records({"R1": [[alt]]})
        (r,) = records
        assert r["pass"] and r["status"] == "needs_confirmation" and r["duration"] is None


# ── metrics ─────────────────────────────────────────────────────────────────

class TestMetrics:
    def test_false_positive_jd_asserted(self):
        # HO03: a structurally valid but dishonest TRANSLATION alignment ("Equine" -> "بيطري") is still the model's
        # audited judgment (translation stays trust-bearing in s1-5.2); the harness scores it as a false positive
        bad = json.loads(br.oracle_response(HO_BY_ID["HO03"]))
        bad["criteria"][0]["targets"] = [{
            "hint": "T1", "type": "role", "match": "equivalent", "jd_span": {"line": 2, "text": "كطبيب بيطري"},
            "alignment": [{"hint": "Equine", "jd": "بيطري", "relation": "translation"},
                          {"hint": "Veterinarian", "jd": "طبيب", "relation": "translation"}], "jd_extra": []}]
        bad = json.dumps(bad, ensure_ascii=False)
        records, cases = scripted_records({"HO03": [[bad]], "HO02": None, "HO04": None},
                                          cases=[HO_BY_ID[c] for c in ("HO03", "HO02", "HO04")])
        s = br.summarize(records, cases)
        j = s["jd_asserted"]
        assert (j["tp"], j["fp"], j["fn"]) == (2, 1, 0) and j["precision"] == round(2 / 3, 4) and j["recall"] == 1.0
        assert j["false_positives"] == [{"case": "HO03", "run": 1, "target": "Equine Veterinarian",
                                         "mapped_text": "كطبيب بيطري"}]
        ho3 = next(r for r in records if r["case"] == "HO03")
        assert not ho3["pass"] and {"mapping", "provenance", "status"} <= {
            f for f, v in ho3["checks"].items() if not v}
        assert s["semantic_error_runs"] == 1 and s["outcomes"]["ok"] == 3
        assert s["diagnostics"]["jd_asserted_via_translation"] == 3          # measurable for a later decision

    def test_form_candidate_is_never_a_false_positive(self):
        # I1: administration -> support labelled "form" is structurally valid; s1-5.2 keeps it as an unverified
        # candidate (original_ai, equivalence_unverified, needs_confirmation) -> a safe, passing outcome
        cand = with_oracle("I1", targets=[{"hint": "T1", "type": "function", "match": "equivalent",
                                           "jd_span": {"line": 2, "text": "database support"},
                                           "alignment": [{"hint": "database", "jd": "database", "relation": "same"},
                                                         {"hint": "administration", "jd": "support",
                                                          "relation": "form"}],
                                           "jd_extra": []}])
        records, cases = scripted_records({"I1": [[cand]], "B1": None})
        s = br.summarize(records, cases)
        i1 = next(r for r in records if r["case"] == "I1")
        assert i1["pass"] and i1["status"] == "needs_confirmation" and "equivalence_unverified" in i1["reasons"]
        assert [t["provenance"] for t in i1["targets"]] == ["original_ai"]
        assert (s["jd_asserted"]["tp"], s["jd_asserted"]["fp"]) == (1, 0)
        assert s["diagnostics"]["unverified_form_candidates"] == 1
        (oracle,) = scripted_records({"I1": None})[0]                         # the labelled "none" answer
        assert oracle["pass"] and "target_not_in_jd" in oracle["reasons"]

    def test_false_negative_jd_asserted(self):
        miss = with_oracle("B2", targets=[{"hint": "T1", "type": "role", "match": "none", "jd_span": None}])
        records, cases = scripted_records({"B2": [[miss]], "B1": None})
        j = br.summarize(records, cases)["jd_asserted"]
        assert (j["tp"], j["fp"], j["fn"], j["recall"]) == (1, 0, 1, 0.5)

    def test_role_function_confusion(self):
        wrong = with_oracle("F1", policy="explicit_role",
                            targets=[{"hint": "T1", "type": "role", "match": "exact", "jd_span": None}])
        records, cases = scripted_records({"F1": [[wrong]], "A1": None})
        s = br.summarize(records, cases)
        assert s["type_confusion"] == {"function": {"role": 1}, "role": {"role": 1}}
        assert s["policy_accuracy"] == 0.5

    def test_ambiguity_and_status_accuracy(self):
        quiet = with_oracle("O1", ambiguity=[], restrictions=[])   # misses the vagueness
        records, cases = scripted_records({"O1": [[quiet]], "N1": None})
        s = br.summarize(records, cases)
        assert s["ambiguity_accuracy"] == 0.5 and s["status_accuracy"] == 0.5

    def test_span_and_setting_checks(self):
        about = with_oracle("Q2", policy="sector", requirement_spans=[
            {"line": 4, "text": "Minimum 3 years of professional experience"},
            {"line": 1, "text": "We are a leading bank in the region", "experience_requirement": True}],
            setting={"line": 1, "text": "bank"})
        records, _ = scripted_records({"Q2": [[about, about]]})
        (r,) = records
        assert r["outcome"] == "validation"                    # V-anchor rejects the About-us span


class TestErrorClasses:
    def test_repair_accounting(self):
        bad = with_oracle("A1", ambiguity=["unsure"])
        records, cases = scripted_records({"A1": [[bad, br.oracle_response(BY_ID["A1"])]]})
        s = br.summarize(records, cases)
        assert s["repaired_ok_runs"] == 1 and s["repair_calls"] == 1 and s["main_calls"] == 1
        assert records[0]["pass"] and [c["call"] for c in records[0]["calls"]] == ["main", "repair"]
        assert [x["call"] for x in records[0]["raw"]] == ["main", "repair"]

    def test_validation_failure_accounting(self):
        bad = with_oracle("A1", ambiguity=["unsure"])
        records, cases = scripted_records({"A1": [[bad, bad]], "F1": None})
        s = br.summarize(records, cases)
        assert s["outcomes"] == {"ok": 1, "failed_validation": 1, "failed_technical": 0}
        assert s["semantic_error_runs"] == 0 and s["policy_accuracy"] == 1.0        # not scored semantically
        assert s["validation_failures"][0]["case"] == "A1" and s["validation_failures"][0]["errors"]["repair_errors"]

    def test_technical_failure_accounting(self):
        records, cases = scripted_records({"A1": [[RuntimeError("network down")]], "F1": None})
        s = br.summarize(records, cases)
        assert s["outcomes"]["failed_technical"] == 1 and s["semantic_error_runs"] == 0
        assert s["technical_failures"][0]["status_reasons"] == ["ai_unavailable"]
        records, cases = scripted_records({"A1": [[(br.oracle_response(BY_ID["A1"]), "length")]]})
        assert br.summarize(records, cases)["technical_failures"][0]["status_reasons"] == ["output_truncated"]


class TestStability:
    def test_stability_detects_changes(self):
        silent = with_oracle("D1", ambiguity=[], targets=[{"hint": "T1", "type": "role", "match": "exact",
                                                           "jd_span": None}])
        plan = {"D1": [[with_oracle("D1")], [silent], [with_oracle("D1")]],
                "A1": None}
        records, cases = scripted_records(plan, runs=3)
        s = br.summarize(records, cases)
        assert s["unstable_cases"] == [{"case": "D1", "fields": ["ambiguity"]}]
        assert s["stability"]["ambiguity"] == 0.5 and s["stability"]["policy"] == 1.0
        assert s["stability"]["all_fields"] == 0.5

    def test_mapping_presence_and_duration_stability(self):
        plan = {"R1": [[with_oracle("R1")], [with_oracle("R1", duration=None, ambiguity=["multiple_durations"])]],
                "G1": [[with_oracle("G1")], [with_oracle("G1", targets=[
                    {"hint": "T1", "type": "function", "match": "none", "jd_span": None}])]]}
        records, cases = scripted_records(plan, runs=2)
        s = br.summarize(records, cases)
        assert {u["case"]: u["fields"] for u in s["unstable_cases"]} == {
            "G1": ["mapping_offered"], "R1": ["duration", "ambiguity"]}


# ── safety ──────────────────────────────────────────────────────────────────

class TestSafety:
    def test_offline_modes_never_create_a_real_client(self, tmp_path, monkeypatch):
        def boom():
            raise AssertionError("real client created")
        monkeypatch.setattr(br.llm_call, "create_client", boom)
        assert br.main(["--out", str(tmp_path / "d")]) == 0                     # dry-run
        assert not (tmp_path / "d").exists()
        assert br.main(["--out", str(tmp_path / "o"), "--mode", "oracle", "--runs", "1", "--cases", "A1,B1"]) == 0
        res = json.loads((tmp_path / "o" / "results.json").read_text(encoding="utf-8"))
        assert res["summary"]["pass_runs"] == 2 and res["meta"]["prompt_fingerprint"] == "4f22dddb117e"
        assert res["meta"]["temperature"] == 0.0 and res["meta"]["mode"] == "oracle"
        report = (tmp_path / "o" / "report.md").read_text(encoding="utf-8")
        for frag in ("## jd_asserted", "## Stability", "## Role/function confusion", "## Outcomes"):
            assert frag in report

    def test_no_database_or_production_imports(self):
        tree = ast.parse((BACKEND / "scripts" / "s1_boundary_regression.py").read_text(encoding="utf-8"))
        mods = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        mods |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        for m in mods:
            for banned in ("database", "sqlalchemy", "asyncpg", "psycopg", "models", "routers", "workers",
                           "llm_criteria_mapper", "s2_experience"):
                assert banned not in m, m
        src = (BACKEND / "scripts" / "s1_boundary_regression.py").read_text(encoding="utf-8").upper()
        assert not re.search(r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|ALTER\s+TABLE|CREATE\s+TABLE|"
                             r"DROP\s+TABLE|TRUNCATE)\b", src)

    def test_s1_prompt_unchanged(self):
        assert (br.sc.S1_PROMPT_VERSION, br.sc.S1_VERSION, br.clf.prompt_fingerprint()) == (
            "s1-5.2", "1.4.3", "4f22dddb117e")


# ── s1-3: diagnostics, held-out set, prompt independence ───────────────────

HELDOUT = br.load_cases(br.FIXTURE.parent / "s1_heldout_cases.json")
HO_BY_ID = {c["id"]: c for c in HELDOUT}


def fixture_phrases(cases) -> set[str]:
    out = set()
    for c in cases:
        out |= set(c["analysis"]["relevant_roles"])
        for r in c["oracle"].get("restrictions") or []:
            if r["kind"] != "vague":                     # vague words ("relevant") are generic, not fixture terms
                out.add(r["text"])
        for t in c["oracle"].get("targets") or []:
            if t.get("jd_span"):
                out.add(t["jd_span"]["text"])
            if "text" in t:
                out.add(t["text"])
        if c["oracle"].get("setting"):
            out.add(c["oracle"]["setting"]["text"])
    return out


def _tokens(s):
    return re.findall(r"\w+", br.normalize(s))


def _contains_phrase(text, phrase) -> bool:
    t, p = _tokens(text), _tokens(phrase)
    return any(t[i:i + len(p)] == p for i in range(len(t) - len(p) + 1))


class TestS14Diagnostics:
    def test_model_policy_is_diagnostic_only(self):
        # F1: correct function type but the model still emits explicit_role -> derived functional, no repair
        records, cases = scripted_records({"F1": [[with_oracle("F1", policy="explicit_role")]]})
        s = br.summarize(records, cases)
        (r,) = records
        assert r["pass"] and r["policy"] == "functional" and s["repair_calls"] == 0
        assert s["diagnostics"] == {"model_policy_mismatch_main": 1, "repair_attempted_type_change": 0,
                                    "repair_discarded_changes": 0, "equivalent_rejected_by_alignment": 0, "alignment_withdrawn": 0,
                                    "unverified_form_candidates": 0, "jd_asserted_via_translation": 0}

    def test_repair_type_change_outside_scope_is_discarded(self):
        bad = with_oracle("F1", ambiguity=["unsure"])                              # only ambiguity is invalid
        flipped = with_oracle("F1", targets=[{"hint": "T1", "type": "role", "match": "exact", "jd_span": None}])
        records, cases = scripted_records({"F1": [[bad, flipped]]})
        s = br.summarize(records, cases)
        (r,) = records
        assert r["pass"] and r["repair_used"] and [t["type"] for t in r["targets"]] == ["function"]
        assert s["diagnostics"] == {"model_policy_mismatch_main": 0, "repair_attempted_type_change": 1,
                                    "repair_discarded_changes": 1, "equivalent_rejected_by_alignment": 0, "alignment_withdrawn": 0,
                                    "unverified_form_candidates": 0, "jd_asserted_via_translation": 0}

    def test_policy_sensitive_mapping_excluded_from_gate(self):
        pm = with_oracle("K2", targets=[{"hint": "T1", "type": "role", "match": "equivalent",
                                         "jd_span": {"line": 2, "text": "P.M."},
                                         "alignment": [{"hint": "PM", "jd": "P.M.", "relation": "abbreviation"}],
                                         "jd_extra": []}])
        records, cases = scripted_records({"K2": [[pm]], "B1": None})
        s = br.summarize(records, cases)
        assert all(r["pass"] for r in records)                                    # K2 alternative accepted
        j = s["jd_asserted"]
        assert (j["tp"], j["fp"]) == (1, 0) and j["policy_sensitive_excluded"] == [
            {"case": "K2", "run": 1, "target": "PM", "jd_asserted": True}]

    def test_dropped_qualifier_is_rejected_by_alignment(self):
        # C2: "Construction" has no counterpart in "كمدير مشروع" -> structural rejection, repair withdraws to none
        drop = with_oracle("C2", targets=[{"hint": "T1", "type": "role", "match": "equivalent",
                                           "jd_span": {"line": 2, "text": "كمدير مشروع"},
                                           "alignment": [{"hint": "Manager", "jd": "كمدير", "relation": "translation"},
                                                         {"hint": "Project", "jd": "مشروع", "relation": "translation"}],
                                           "jd_extra": []}])
        records, cases = scripted_records({"C2": [[drop, br.oracle_response(BY_ID["C2"])]]})
        s = br.summarize(records, cases)
        (r,) = records
        assert r["pass"] and r["repair_used"] and s["jd_asserted"]["fp"] == 0
        assert s["diagnostics"]["equivalent_rejected_by_alignment"] == 1

    def test_repeated_dishonest_alignment_is_withdrawn(self):
        # C2: the repair repeats the dropped-qualifier claim -> s1-5.1 withdraws it -> the labelled outcome
        drop = with_oracle("C2", targets=[{"hint": "T1", "type": "role", "match": "equivalent",
                                           "jd_span": {"line": 2, "text": "كمدير مشروع"},
                                           "alignment": [{"hint": "Construction", "jd": "مشروع", "relation": "translation"},
                                                         {"hint": "Project", "jd": "مشروع", "relation": "same"},
                                                         {"hint": "Manager", "jd": "مدير", "relation": "same"}],
                                           "jd_extra": []}])
        records, cases = scripted_records({"C2": [[drop, drop]]})
        s = br.summarize(records, cases)
        (r,) = records
        assert r["outcome"] == "ok" and r["pass"] and r["withdrawals"][0]["hint"] == "T1"
        assert s["diagnostics"]["alignment_withdrawn"] == 1 and s["jd_asserted"]["fp"] == 0

    def test_match_counts(self):
        records, cases = scripted_records({}, cases=CASES)
        assert br.summarize(records, cases)["match_counts"] == {"exact": 10, "equivalent": 7, "none": 15}


class TestHeldOut:
    def test_size_families_and_schema(self):
        assert len(HELDOUT) >= 12 and len({c["id"] for c in HELDOUT}) == len(HELDOUT)
        fams = {c["family"] for c in HELDOUT}
        for f in ("HO_translated_role", "HO_translated_function", "HO_grammatical", "HO_abbreviation",
                  "HO_qualifier_dropped", "HO_qualifier_added", "HO_adjacent", "HO_seniority", "HO_works_with",
                  "HO_typing"):
            assert f in fams, f
        mapped = [t["mapped"] for c in HELDOUT for t in c["expected"]["targets"]]
        assert sum(mapped) >= 3 and mapped.count(False) >= 8
        assert [c["id"] for c in HELDOUT if "equivalence_unverified" in c["expected"].get("reasons_include", [])] == [
            "HO06"]
        assert sum(1 for c in HELDOUT if any(re.search(r"[؀-ۿ]", ln) for ln in c["jd_lines"])) >= 4

    def test_oracle_answers_score_100_percent(self):
        records, cases = scripted_records({}, cases=HELDOUT)
        assert all(r["outcome"] == "ok" and r["pass"] for r in records), [
            (r["case"], r.get("checks")) for r in records if not r.get("pass")]

    def test_vocabulary_disjoint_from_main_fixture(self):
        main, held = fixture_phrases(CASES), fixture_phrases(HELDOUT)
        for h in held:
            assert not any(_contains_phrase(m, h) or _contains_phrase(h, m) for m in main), h


def _words(s):
    from services.s1_requirements.jd_text import words
    return set(words(s))


def fixture_answer_pairs(cases) -> set[tuple[str, str]]:
    """Lexical pairs that encode a labelled answer (s1-5.2 leakage guard), never single generic words:
    - every oracle alignment pair whose two sides differ (hint word -> the JD word that answers it);
    - an "X vs Y" case description, but only when X's words occur in the case's own hint text and Y's in its
      JD text (or the reverse): that is the case's contrast pair, e.g. administration / support.
    Generic descriptions ("noun vs verb form") never yield pairs: their words are not the case's vocabulary."""
    pairs = set()
    for c in cases:
        for t in c["oracle"].get("targets") or []:
            for p in t.get("alignment") or []:
                for h in _words(p["hint"]):
                    for j in _words(p["jd"]):
                        if h != j:
                            pairs.add((h, j))
        desc = c.get("description") or ""
        if " vs " not in desc:
            continue
        left, right = (_words(x) for x in desc.split(" vs ", 1))
        hint_w = set().union(*(_words(h) for h in c["analysis"]["relevant_roles"])) if c["analysis"][
            "relevant_roles"] else set()
        jd_w = set().union(*(_words(ln) for ln in c["jd_lines"]))
        for a_side, b_side in ((left - right, right - left), (right - left, left - right)):
            if a_side and b_side and a_side <= hint_w and b_side <= jd_w:
                pairs |= {(x, y) for x in a_side for y in b_side}
    return pairs


def leaked_pairs(prompt: str, pairs) -> list[tuple[str, str]]:
    """Answer pairs whose two words occur together in one prompt line."""
    lines = [_words(ln) for ln in prompt.splitlines()]
    return sorted(p for p in pairs if any(p[0] in ln and p[1] in ln for ln in lines))


class TestPromptIndependence:
    BANNED = ("مدير مشروع إنشائي", "مهندس موقع", "محاسب", "Senior Accountant", "Assistant Project Manager",
              "enterprise software", "database support", "project coordination", "nursing", "leading bank",
              "oil and gas", "banking", "construction")

    def test_prompt_leaks_no_fixture_answer_pairs(self):
        pairs = fixture_answer_pairs(CASES) | fixture_answer_pairs(HELDOUT)
        assert ("administration", "support") in pairs and ("management", "coordination") in pairs
        assert ("noun", "verb") not in pairs                                     # generic descriptions ignored
        assert leaked_pairs(br.clf.S1_SYSTEM_PROMPT, pairs) == []

    def test_leak_detector_catches_the_s1_5_1_contamination(self):
        pairs = fixture_answer_pairs(CASES)
        old = ("form  the same word ... (administration is not a form of support; management is not a form of "
               "coordination).")
        assert leaked_pairs(old, pairs) == [("administration", "support"), ("management", "coordination")]
        assert leaked_pairs("administration\nsupport", pairs) == []           # separate lines: generic use is fine

    def test_prompt_uses_no_fixture_phrases(self):
        prompt = br.clf.S1_SYSTEM_PROMPT
        for phrase in sorted(fixture_phrases(CASES) | fixture_phrases(HELDOUT) | set(self.BANNED)):
            assert not _contains_phrase(prompt, phrase), phrase
