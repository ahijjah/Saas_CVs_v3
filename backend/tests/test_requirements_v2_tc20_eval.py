"""Offline tests of the bounded evaluation executor and its frozen labels (no network, no paid calls).

They prove: the labels map every finding to a check and back, the labels file is the frozen one, the scorer's checks are
sensitive (a stored answer fails exactly the eight checks; a reference answer passes all; single changes flip one check),
the plan stays under the cap, no call is retried, the prompts are the pinned files, the v2-4 candidate keeps the v2-3
output contract, and the 12-case benchmark reproduces its stored gates read-only. Synthetic answers prove none of
model compliance; only the approved run can.
"""
import hashlib
import json
import pathlib
import re

import pytest

import scripts.requirements_v2_tc20_eval as E

FIX = pathlib.Path(__file__).parent / "fixtures" / "requirements_v2_technical_coordinator_20"
LABELS_SHA256 = "6a87da80e142640af1327c4a3fd5a21adb648726a9733a699fa8d3ec67135ef9"


@pytest.fixture(scope="module")
def jd_and_labels():
    return E.load_inputs()


@pytest.fixture(scope="module")
def stored_raw():
    return json.loads((FIX / "evidence.json").read_text(encoding="utf-8"))["pipeline"]["raw_response"]["text"]


def reference_answer(jd, labels):
    """A synthetic answer written from the frozen expected values (for the sensitivity tests only)."""
    ev_vals = labels["expected_values"]
    resp = [{"text": d.rstrip("."), "importance": "required", "importance_cue": None, "source_text": d, "origin": "from_responsibilities",
             "alternatives": None, "experience": None} for d in ev_vals["duty_lines"]]
    comp = [{"text": c.rstrip("."), "importance": "required", "importance_cue": None, "source_text": c, "origin": "stated",
             "alternatives": None, "experience": None} for c in ev_vals["competency_lines"]]
    exp_or = {"text": "1–3 years of professional experience in ICT systems support, business applications, digital platforms, or software implementation projects",
              "importance": "required", "importance_cue": None, "origin": "stated", "alternatives": ev_vals["experience_or_alternatives"],
              "source_text": "1–3 years of professional experience in ICT systems support, business applications, digital platforms, or software implementation projects",
              "experience": {"subject": "ICT systems support", "min_years": 1}}
    fam = {"text": "Familiarity with business process documentation, system integrations, databases, reporting tools, or enterprise applications",
           "importance": "preferred", "importance_cue": "is an advantage", "origin": "stated",
           "alternatives": ["business process documentation", "system integrations", "databases", "reporting tools", ev_vals["familiarity_or_must_include"]],
           "source_text": "Familiarity with business process documentation, system integrations, databases, reporting tools, or enterprise applications is an advantage",
           "experience": None}
    local = {"text": "Knowledge of the local business and regulatory environment", "importance": "preferred", "importance_cue": "is an advantage",
             "origin": "stated", "alternatives": None, "experience": None, "source_text": "Knowledge of the local business and regulatory environment is an advantage"}
    soft = [{"text": t, "importance": "required", "importance_cue": None, "origin": "stated", "alternatives": None, "experience": None, "source_text": s}
            for t, s in (("Strong communication", "Strong communication, coordination, and teamwork skills"),
                         ("Coordination skills", "Strong communication, coordination, and teamwork skills"),
                         ("Teamwork skills", "Strong communication, coordination, and teamwork skills"))]
    return json.dumps({"scoreability": {"status": "scoreable", "reason": ""},
                       "categories": {"skills": comp, "experience": resp + [exp_or, fam], "education": [], "certifications": [],
                                      "soft_skills": soft, "domain_knowledge": [local], "other_requirements": []},
                       "category_weights": {"skills": 30, "experience": 30, "education": 20, "certifications": 0, "soft_skills": 20,
                                            "domain_knowledge": 0, "other_requirements": 0},
                       "non_scoreable_requirements": [{"text": "Work at the Ramallah office", "category": "location", "reason": "x",
                                                       "source_text": ev_vals.get("location_source", "It is expected that the Technical Coordinator. will work at the MoNE office in Ramallah and as needed, at other MoNE offices in the West Bank to support operational and implementation activities.")}],
                       "post_hiring_conditions": [],
                       "informational_items": [{"text": "Submit deliverables", "category": "reporting_line", "reason": "x",
                                                "source_text": "The Coordinator shall submit deliverables to the IPSD II Technical Resident Advisor and the IPSD II Project Director for review and approval."}],
                       "warnings": []})


# ── labels: frozen, and every finding maps to a check and back ──────────────────────────────────────────────────────────

def test_labels_file_is_the_frozen_one(jd_and_labels):
    assert hashlib.sha256((FIX / "expected_labels.json").read_bytes()).hexdigest() == LABELS_SHA256


def test_every_finding_maps_to_one_check_and_every_check_to_findings(jd_and_labels):
    _, labels = jd_and_labels
    findings, checks = labels["findings"], labels["checks"]
    for fid, f in findings.items():
        assert f["check"] in checks and fid in checks[f["check"]]["findings"], fid
    for cid, c in checks.items():
        assert c["findings"] and all(findings[f]["check"] == cid for f in c["findings"]), cid
    assert len(findings) == 10 and len(checks) == 8


def test_each_check_names_an_existing_regression_test(jd_and_labels):
    _, labels = jd_and_labels
    source = (pathlib.Path(__file__).parent / "test_requirements_v2_tc20_regression.py").read_text(encoding="utf-8")
    for cid, c in labels["checks"].items():
        assert re.search(rf"def {c['test']}\(", source), (cid, c["test"])


# ── the scorer: sensitivity ─────────────────────────────────────────────────────────────────────────────────────────────

def test_the_stored_answer_fails_exactly_the_eight_checks(jd_and_labels, stored_raw):
    jd, labels = jd_and_labels
    s = E.score_answer(stored_raw, jd, labels)
    assert s["valid_json"] and s["items"] == 15
    assert s["checks"] == {k: False for k in labels["checks"]}
    assert s["invented"] == {"source_not_verbatim": 0, "duty_not_a_duty_line": 0} and s["warnings"] == 0


def test_a_reference_answer_passes_every_check(jd_and_labels):
    jd, labels = jd_and_labels
    s = E.score_answer(reference_answer(jd, labels), jd, labels)
    assert s["checks"] == {k: True for k in labels["checks"]}, s["checks"]
    assert s["invented"] == {"source_not_verbatim": 0, "duty_not_a_duty_line": 0}


@pytest.mark.parametrize("mutate,flipped", [
    (lambda d: d["categories"].__setitem__("experience", [i for i in d["categories"]["experience"] if i.get("origin") != "from_responsibilities"]), "C_RESP"),
    (lambda d: d["categories"].__setitem__("skills", d["categories"]["skills"][1:]), "C_COMP"),
    (lambda d: d["categories"]["experience"][-1].__setitem__("alternatives", None), "C_FAM_OR"),
    (lambda d: d["categories"]["domain_knowledge"].clear() or d["categories"]["experience"].append({"text": "Knowledge of the local business and regulatory environment", "importance": "preferred", "importance_cue": "is an advantage", "origin": "stated", "alternatives": None, "experience": None, "source_text": "Knowledge of the local business and regulatory environment is an advantage"}), "C_LOCAL"),
    (lambda d: d.__setitem__("non_scoreable_requirements", []), "C_LOCATION"),
    (lambda d: d.__setitem__("informational_items", []), "C_REPORTING"),
    (lambda d: d["categories"].__setitem__("soft_skills", [{"text": "Strong communication, coordination, and teamwork skills", "importance": "required", "importance_cue": None, "origin": "stated", "alternatives": None, "experience": None, "source_text": "Strong communication, coordination, and teamwork skills"}]), "C_AND"),
])
def test_removing_one_thing_flips_exactly_its_check(jd_and_labels, mutate, flipped):
    jd, labels = jd_and_labels
    d = json.loads(reference_answer(jd, labels))
    mutate(d)
    s = E.score_answer(json.dumps(d), jd, labels)
    assert s["checks"][flipped] is False, flipped
    assert [k for k, v in s["checks"].items() if not v] == [flipped]


def test_an_invented_duty_and_a_non_verbatim_source_are_counted(jd_and_labels):
    jd, labels = jd_and_labels
    d = json.loads(reference_answer(jd, labels))
    d["categories"]["experience"].append({"text": "Invented", "importance": "required", "importance_cue": None, "origin": "from_responsibilities",
                                          "alternatives": None, "experience": None, "source_text": "Manage the payroll of the ministry."})
    s = E.score_answer(json.dumps(d), jd, labels)
    assert s["invented"]["duty_not_a_duty_line"] == 1 and s["invented"]["source_not_verbatim"] == 1


def test_invalid_json_fails_every_check_and_is_counted(jd_and_labels):
    jd, labels = jd_and_labels
    s = E.score_answer("{not json", jd, labels)
    assert s["valid_json"] is False and not any(s["checks"].values())
    assert E.report([{"arm": "v2-3", "scored": s}], labels)["v2-3"]["invalid_json"] == 1


# ── the plan, the cap, no retries, no network in dry-run ───────────────────────────────────────────────────────────────

def test_the_plan_is_ten_calls_under_the_cap(jd_and_labels):
    jd, _ = jd_and_labels
    p = E.plan(jd)
    assert len(p["calls"]) == 10 and p["retries"] == 0 and p["model"] == "gpt-4o-mini-2024-07-18"
    assert p["worst_case_total_usd"] <= E.CAP_USD == 0.10


def test_a_cap_below_the_worst_case_refuses_to_plan(jd_and_labels, monkeypatch):
    jd, _ = jd_and_labels
    monkeypatch.setattr(E, "CAP_USD", 0.01)
    with pytest.raises(E.PlanError):
        E.plan(jd)


def test_dry_run_makes_no_call(jd_and_labels, monkeypatch, capsys):
    monkeypatch.setattr(E, "openai_call", lambda m: (_ for _ in ()).throw(AssertionError("network")))
    assert E.main([]) == 0
    assert "dry-run" in capsys.readouterr().out


def test_a_failed_call_is_recorded_and_never_retried(jd_and_labels, tmp_path, stored_raw):
    jd, labels = jd_and_labels
    attempts = []

    def api(messages):
        attempts.append(1)
        if len(attempts) == 2:
            raise TimeoutError("simulated")
        return {"raw": stored_raw, "finish_reason": "stop", "model": E.MODEL, "usage": {"prompt_tokens": 6000, "completion_tokens": 900}}

    res = E.run(tmp_path / "run", jd, labels, api)
    recs = [json.loads(x) for x in (tmp_path / "run" / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(attempts) == 10 and len(recs) == 10                       # one attempt per planned call, no retry of the failed one
    assert [r["error"] for r in recs].count("TimeoutError") == 1
    assert res["spent_usd"] < E.CAP_USD


def test_the_guard_refuses_the_first_call_when_the_cap_cannot_hold_the_plan(jd_and_labels, tmp_path, stored_raw, monkeypatch):
    """The before-call guard, exercised directly: a plan whose worst case cannot fit the cap makes the run refuse the call."""
    jd, labels = jd_and_labels
    tiny = {"calls": [{"arm": "v2-3", "call": n, "worst_case_usd": 0.001} for n in range(1, 4)], "worst_case_total_usd": 0.003}
    monkeypatch.setattr(E, "plan", lambda _jd: tiny)
    monkeypatch.setattr(E, "CAP_USD", 0.0)
    calls = []
    E.run(tmp_path / "guard", jd, labels, lambda m: calls.append(1) or {"raw": stored_raw})
    assert calls == []


def test_a_run_with_no_usage_spends_nothing_and_stays_under_the_cap(jd_and_labels, tmp_path, stored_raw):
    jd, labels = jd_and_labels
    res = E.run(tmp_path / "free", jd, labels, lambda m: {"raw": stored_raw, "finish_reason": "stop", "model": E.MODEL, "usage": {}})
    assert res["spent_usd"] == 0 and res["calls_made"] == 10 and res["spent_usd"] <= E.CAP_USD


def test_missing_key_or_out_refuses_execute(jd_and_labels, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        E.main(["--execute"])


# ── the prompts: pinned files; v2-4 keeps the v2-3 output contract and carries no job or benchmark wording ──────────

def test_a_changed_prompt_file_is_refused(jd_and_labels, monkeypatch):
    jd, _ = jd_and_labels
    monkeypatch.setitem(E.ARMS["v2-4"], "sha256", "0" * 64)
    with pytest.raises(E.PlanError):
        E.messages_for("v2-4", jd)


def test_the_executor_user_message_is_the_live_one():
    from services.requirements_v2.extraction.prompt import build_user_message   # test-only import
    jd, _ = E.load_inputs()
    assert E.user_message(jd) == build_user_message(jd, None)


def test_v24_manifest_matches_the_file():
    import json as _json
    m = _json.loads((E.ARMS["v2-4"]["file"].parent / "MANIFEST.json").read_text(encoding="utf-8"))
    assert hashlib.sha256(E.ARMS["v2-4"]["file"].read_bytes()).hexdigest() == m["sha256"] == E.ARMS["v2-4"]["sha256"]
    assert m["base_sha256"] == E.ARMS["v2-3"]["sha256"]


def test_v24_keeps_the_v23_output_contract_byte_for_byte():
    v23 = (E.ARMS["v2-3"]["file"]).read_text(encoding="utf-8")
    v24 = (E.ARMS["v2-4"]["file"]).read_text(encoding="utf-8")
    seg = lambda t, a, b: t[t.index(a):t.index(b)]
    assert seg(v23, "OUTPUT FORMAT", "\nRULES") == seg(v24, "OUTPUT FORMAT", "\nRULES")
    assert seg(v23, "ITEM\n", "RULES") == seg(v24, "ITEM\n", "RULES")


def _shingles(text, n=5):
    words = re.findall(r"\w+", text.lower())
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def _added_text() -> str:
    """The lines v2-4 adds to v2-3 (added lines only; inherited text is not the candidate's wording)."""
    import difflib
    a = (E.ARMS["v2-3"]["file"]).read_text(encoding="utf-8").splitlines()
    b = (E.ARMS["v2-4"]["file"]).read_text(encoding="utf-8").splitlines()
    return "\n".join(line[2:] for line in difflib.ndiff(a, b) if line.startswith("+ "))


def _job_texts():
    texts = [json.loads(E.EVIDENCE.read_text(encoding="utf-8"))["description"]]
    texts += [json.loads(p.read_text(encoding="utf-8"))["jd"] for p in sorted((FIX.parents[0] / "requirements_v2_benchmark" / "cases").glob("*.json"))]
    return texts


def test_the_added_candidate_wording_shares_no_five_word_phrase_with_any_jd_or_benchmark_case():
    """Leakage check on what v2-4 adds. Phrases already in v2-3 (its illustrative examples and rule 4 count lines) are
    inherited and are not checked here; test_inherited_overlap_is_recorded documents them."""
    added = _added_text()
    assert added.strip(), "the candidate adds text"
    shared = set()
    for jd in _job_texts():
        shared |= _shingles(jd) & _shingles(added)
    assert not shared, sorted(shared)[:10]


def test_inherited_overlap_is_recorded():
    v23 = (E.ARMS["v2-3"]["file"]).read_text(encoding="utf-8")
    inherited = set()
    for jd in _job_texts():
        inherited |= _shingles(jd) & _shingles(v23)
    assert inherited, "v2-3 examples overlap JDs: the overlap is inherited, not introduced by v2-4"


# ── the 12-case benchmark: offline regression, read-only ───────────────────────────────────────────────────────────────

def test_the_twelve_case_benchmark_reproduces_its_stored_gates():
    r = E.offline_regression()
    assert r["cases"] == 12 and r["gates_match_stored"] is True and r["mismatches"] == []
