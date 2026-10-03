"""Offline tests for the S1 s1-2 boundary-regression harness (no API, no database)."""
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
        assert sum(mapped) >= 7 and mapped.count(False) >= 15

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
        # C2: model maps the generic Arabic "كمدير مشروع" (valid span, wrong semantics)
        bad = with_oracle("C2", targets=[{"hint": "T1", "type": "role",
                                          "jd_span": {"line": 2, "text": "كمدير مشروع"}}])
        records, cases = scripted_records({"C2": [[bad]], "B1": None, "G1": None})
        s = br.summarize(records, cases)
        j = s["jd_asserted"]
        assert (j["tp"], j["fp"], j["fn"]) == (2, 1, 0) and j["precision"] == round(2 / 3, 4) and j["recall"] == 1.0
        assert j["false_positives"] == [{"case": "C2", "run": 1, "target": "Construction Project Manager",
                                         "mapped_text": "كمدير مشروع"}]
        c2 = next(r for r in records if r["case"] == "C2")
        assert not c2["pass"] and {"mapping", "provenance", "status"} <= {
            f for f, v in c2["checks"].items() if not v}
        assert s["semantic_error_runs"] == 1 and s["outcomes"]["ok"] == 3

    def test_false_negative_jd_asserted(self):
        miss = with_oracle("G1", targets=[{"hint": "T1", "type": "function", "jd_span": None}])
        records, cases = scripted_records({"G1": [[miss]], "B1": None})
        j = br.summarize(records, cases)["jd_asserted"]
        assert (j["tp"], j["fp"], j["fn"], j["recall"]) == (1, 0, 1, 0.5)

    def test_role_function_confusion(self):
        wrong = with_oracle("F1", policy="explicit_role", targets=[{"hint": "T1", "type": "role", "jd_span": None}])
        records, cases = scripted_records({"F1": [[wrong]], "A1": None})
        s = br.summarize(records, cases)
        assert s["type_confusion"] == {"function": {"role": 1}, "role": {"role": 1}}
        assert s["policy_accuracy"] == 0.5

    def test_ambiguity_and_status_accuracy(self):
        quiet = with_oracle("O1", ambiguity=[])
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
        bad = with_oracle("A1", policy="nope")
        records, cases = scripted_records({"A1": [[bad, br.oracle_response(BY_ID["A1"])]]})
        s = br.summarize(records, cases)
        assert s["repaired_ok_runs"] == 1 and s["repair_calls"] == 1 and s["main_calls"] == 1
        assert records[0]["pass"] and [c["call"] for c in records[0]["calls"]] == ["main", "repair"]
        assert [x["call"] for x in records[0]["raw"]] == ["main", "repair"]

    def test_validation_failure_accounting(self):
        bad = with_oracle("A1", policy="nope")
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
        plan = {"D1": [[with_oracle("D1")], [with_oracle("D1", ambiguity=[])], [with_oracle("D1")]],
                "A1": None}
        records, cases = scripted_records(plan, runs=3)
        s = br.summarize(records, cases)
        assert s["unstable_cases"] == [{"case": "D1", "fields": ["ambiguity"]}]
        assert s["stability"]["ambiguity"] == 0.5 and s["stability"]["policy"] == 1.0
        assert s["stability"]["all_fields"] == 0.5

    def test_mapping_presence_and_duration_stability(self):
        plan = {"R1": [[with_oracle("R1")], [with_oracle("R1", duration=None, ambiguity=["multiple_durations"])]],
                "G1": [[with_oracle("G1")], [with_oracle("G1", targets=[
                    {"hint": "T1", "type": "function", "jd_span": None}])]]}
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
        assert res["summary"]["pass_runs"] == 2 and res["meta"]["prompt_fingerprint"] == "e64eb1a979e9"
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
            "s1-2", "1.1.0", "e64eb1a979e9")
