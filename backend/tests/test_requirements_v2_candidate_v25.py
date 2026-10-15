"""Offline tests of criteria_extraction_v2-5 (the candidate) and of its bounded evaluation. No network, no paid call.

They prove: the candidate keeps the v2-3 output contract byte for byte, changes only the two category lines and adds text
(the diff), the frozen prompts are untouched, the added wording and the worked examples share no five-word phrase with the TC20
JD, the benchmark or the new evaluation JDs, every worked example is consistent with the new rules, the frozen evaluation set
matches its hashes and its spans are verbatim, the expected-output scorer detects each failure group and passes a reference
answer, and the protected run and its plan behave as specified.
"""
import difflib
import hashlib
import json
import pathlib
import re

import pytest

import scripts.requirements_v2_candidate_eval as E
import scripts.requirements_v2_tc20_compare as cmp
import scripts.requirements_v2_tc20_eval as ev

BACKEND = pathlib.Path(__file__).resolve().parents[1]
CAND = BACKEND / "prompt_candidates" / "criteria_extraction_v2-5" / "criteria_extraction_v2-5.txt"
BASE = BACKEND / "prompt_candidates" / "criteria_extraction_v2-3" / "criteria_extraction_v2-3.txt"
V24 = BACKEND / "prompt_candidates" / "criteria_extraction_v2-4" / "criteria_extraction_v2-4.txt"
EVAL = BACKEND / "tests" / "fixtures" / "requirements_v2_candidate_v25" / "eval_set"
BENCH = BACKEND / "tests" / "fixtures" / "requirements_v2_benchmark" / "cases"
CATEGORIES = ("skills", "experience", "education", "certifications", "soft_skills", "domain_knowledge", "other_requirements")
TERMS = ["ABRS", "MoNE", "IPSD", "Ramallah", "West Bank", "Technical Coordinator", "Automated Business Registry", "PIA"]


def sha(path_or_bytes):
    b = path_or_bytes if isinstance(path_or_bytes, bytes) else path_or_bytes.read_bytes()
    return hashlib.sha256(b).hexdigest()


def shingles(text, n=5):
    toks = re.findall(r"\w+", text.casefold())
    return {" ".join(toks[i:i + n]) for i in range(len(toks) - n + 1)}


def tc20_and_benchmark_jds():
    texts = [ev.load_inputs()[0]]
    texts += [json.loads(p.read_text(encoding="utf-8"))["jd"] for p in sorted(BENCH.glob("*.json"))]
    return texts


def added_lines(base, cand):
    return [l[1:] for l in difflib.ndiff(base.splitlines(), cand.splitlines()) if l.startswith("+ ")]


@pytest.fixture(scope="module")
def texts():
    return BASE.read_text(encoding="utf-8"), CAND.read_text(encoding="utf-8")


# ── 1. the contract and the diff ─────────────────────────────────────────────────────────────────────────────────────

def test_the_candidate_pin_and_the_frozen_prompts_are_unchanged():
    assert sha(CAND) == E.CANDIDATE_SHA256 == "f1569a8b400257db20b1c0b24fd7728605fe472eef7fff993a384f12f0cbc8db"
    assert sha(BASE) == "21a2f9420c12e1a43579c6817b6a8601547d92b653cd432a693ef590b784b43b"
    assert sha(V24) == "dd2651bcda14ae816c89d33dd08ecb4aefa5d63e2e51dfbe7d2b39f8b1989a9e"


def test_the_output_contract_is_byte_identical_to_v23(texts):
    base, cand = texts
    start, end = base.index("OUTPUT FORMAT"), base.index("\nRULES\n")
    cstart, cend = cand.index("OUTPUT FORMAT"), cand.index("\nRULES\n")
    assert cand[cstart:cend] == base[start:end]


def test_only_the_two_category_lines_are_replaced_and_everything_else_is_added(texts):
    base, cand = texts
    removed = [l[2:] for l in difflib.ndiff(base.splitlines(), cand.splitlines()) if l.startswith("- ")]
    assert [l.split(":")[0].strip() for l in removed] == ["skills", "soft_skills"]
    assert all(l.startswith("   ") for l in removed)


def test_the_diff_file_is_the_exact_difference(texts):
    base, cand = texts
    patch = (CAND.parent / "DIFF.patch").read_text(encoding="utf-8")
    recomputed = "".join(difflib.unified_diff(base.splitlines(keepends=True), cand.splitlines(keepends=True),
                                              "a/criteria_extraction_v2-3.txt", "b/criteria_extraction_v2-5.txt"))
    assert [l for l in patch.splitlines() if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))] == \
           [l for l in recomputed.splitlines() if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))]


def test_the_manifest_pins_this_candidate():
    m = json.loads((CAND.parent / "MANIFEST.json").read_text(encoding="utf-8"))
    assert m["sha256"] == sha(CAND) and m["base_sha256"] == sha(BASE) and m["status"].startswith("offline candidate")
    assert m["version"] == "criteria_extraction_v2-5"


# ── 2. the vocabulary and the leakage guards ─────────────────────────────────────────────────────────────────────────

def test_the_candidate_adds_no_tc20_or_benchmark_vocabulary(texts):
    base, cand = texts
    added = "\n".join(added_lines(base, cand))
    for term in TERMS:
        assert not re.search(r"\b" + re.escape(term) + r"\b", cand), term
    assert added.strip()


def test_the_added_wording_shares_no_five_word_phrase_with_the_tc20_jd_or_the_benchmark(texts):
    base, cand = texts
    added = "\n".join(added_lines(base, cand))
    shared = set()
    for jd in tc20_and_benchmark_jds():
        shared |= shingles(jd) & shingles(added)
    assert not shared, sorted(shared)[:5]


def test_the_added_wording_shares_no_five_word_phrase_with_the_evaluation_jds(texts):
    base, cand = texts
    added = "\n".join(added_lines(base, cand))
    shared = set()
    for p in sorted((EVAL / "jds").glob("*.txt")):
        shared |= shingles(p.read_text(encoding="utf-8")) & shingles(added)
    assert not shared, sorted(shared)[:5]


# ── 3. the worked examples are consistent with the rules ─────────────────────────────────────────────────────────────

def _examples(cand):
    """(jd_text, answer_dict) for every example that carries a JSON answer, in order."""
    lines = cand.splitlines()
    out, jd_lines, pending_quote = [], None, None
    for line in lines:
        if line.startswith("Job description: \"") or line.startswith("Job description (Arabic): \""):
            pending_quote = line.split(": ", 1)[1].strip().strip('"')
        if line == "<<<JD":
            jd_lines = []
            continue
        if line == "JD>>>":
            pending_quote = "\n".join(jd_lines)
            jd_lines = None
            continue
        if jd_lines is not None:
            jd_lines.append(line)
        if line.startswith('{"scoreability"'):
            out.append((pending_quote, json.loads(line)))
    return out


def test_every_worked_example_answer_is_valid_json_with_the_contract_keys(texts):
    _, cand = texts
    examples = _examples(cand)
    assert len(examples) == 6                       # the original five plus the new one
    for jd, ans in examples:
        assert set(ans) == {"scoreability", "categories", "category_weights", "non_scoreable_requirements",
                            "post_hiring_conditions", "informational_items", "warnings"}
        assert set(ans["categories"]) == {"skills", "experience", "education", "certifications", "soft_skills", "domain_knowledge",
                                          "other_requirements"}


def test_every_worked_example_quotes_verbatim_and_follows_the_item_rules(texts):
    _, cand = texts
    for jd, ans in _examples(cand):
        for cat, items in ans["categories"].items():
            for it in items:
                assert it["source_text"] in jd, (cat, it["source_text"])
                assert set(it) == {"text", "importance", "importance_cue", "source_text", "origin", "alternatives", "experience"}
                if it["importance"] == "required":
                    assert it["importance_cue"] is None
                else:
                    assert it["importance_cue"] and it["importance_cue"] in jd
                if it["alternatives"] is not None:
                    assert len(it["alternatives"]) >= 2
                    assert all(a in jd for a in it["alternatives"]), it["alternatives"]
                if it["experience"] is not None:
                    assert cat == "experience"
        for name in ("non_scoreable_requirements", "post_hiring_conditions", "informational_items"):
            for row in ans[name]:
                assert row["source_text"] in jd


def test_the_new_example_shows_the_rules_it_is_meant_to_teach(texts):
    _, cand = texts
    jd, ans = _examples(cand)[-1]
    assert "Key duties" in jd and "Receiving:" in jd and "Dispatch:" in jd           # sub-headed duties
    origins = [it["origin"] for it in ans["categories"]["experience"]]
    assert origins == ["from_responsibilities"] * 3                                  # every line of both groups
    cert = ans["categories"]["certifications"][0]
    assert cert["alternatives"] == ["Forklift licence", "equivalent operating certificate"]   # complete OR as one item
    members = [it for it in ans["categories"]["soft_skills"]]
    assert [m["text"] for m in members] == ["Communication", "Teamwork"]            # AND as separate items
    assert all(m["alternatives"] is None and m["source_text"] == "Communication and teamwork skills." for m in members)
    dom = ans["categories"]["domain_knowledge"][0]
    assert dom["importance"] == "preferred" and dom["importance_cue"] == "Preferred"  # item-level importance under a heading


# ── 4. the frozen evaluation set ─────────────────────────────────────────────────────────────────────────────────────

def test_the_evaluation_set_matches_its_frozen_hashes():
    m = json.loads((EVAL / "MANIFEST.json").read_text(encoding="utf-8"))
    assert set(m["files"]) == {"jds/eval_en_field_service_01.txt", "expected/eval_en_field_service_01.json",
                               "jds/eval_ar_facilities_01.txt", "expected/eval_ar_facilities_01.json"}
    for rel, digest in m["files"].items():
        assert sha((EVAL / rel)) == digest, rel


@pytest.mark.parametrize("name", ["eval_en_field_service_01", "eval_ar_facilities_01"])
def test_the_expected_outputs_quote_verbatim_and_list_every_alternative(name):
    spec = json.loads((EVAL / "expected" / f"{name}.json").read_text(encoding="utf-8"))
    jd = (EVAL / "jds" / f"{name}.txt").read_text(encoding="utf-8")
    for it in spec["items"]:
        assert it["source"] in jd and it["key"] and it["category"] in CATEGORIES
        if it["alternatives"]:
            assert len(it["alternatives"]) >= 2 and all(a in jd for a in it["alternatives"])
    for c in spec["conditions"]:
        assert c["source"] in jd and c["list"] in E.CONDITION_LISTS


def test_the_expected_outputs_cover_subheaded_duties_or_and_and_routing():
    en = json.loads((EVAL / "expected" / "eval_en_field_service_01.json").read_text(encoding="utf-8"))
    ar = json.loads((EVAL / "expected" / "eval_ar_facilities_01.json").read_text(encoding="utf-8"))
    for spec in (en, ar):
        duties = [i for i in spec["items"] if i["origin"] == "from_responsibilities"]
        assert len(duties) == 3
        assert any(i["alternatives"] for i in spec["items"]) and any(i["group"] == "AND" for i in spec["items"])
        assert any(i["importance"] == "preferred" for i in spec["items"])
        assert {c["list"] for c in spec["conditions"]} == set(E.CONDITION_LISTS)


# ── 5. the expected-output scorer ────────────────────────────────────────────────────────────────────────────────────

def reference_answer(spec):
    """A synthetic answer written from the expected outputs (for the sensitivity tests only)."""
    cats = {c: [] for c in ("skills", "experience", "education", "certifications", "soft_skills", "domain_knowledge", "other_requirements")}
    for it in spec["items"]:
        alts = it["alternatives"]
        cats[it["category"]].append({"text": it["key"], "importance": it["importance"],
                                     "importance_cue": it.get("importance_cue"), "source_text": it["source"], "origin": it["origin"],
                                     "alternatives": alts, "experience": None})
    conds = {n: [] for n in E.CONDITION_LISTS}
    for c in spec["conditions"]:
        conds[c["list"]].append({"text": c["key"], "category": c["category"], "reason": "r", "source_text": c["source"]})
    return json.dumps({"scoreability": {"status": "scoreable", "reason": ""}, "categories": cats,
                       "category_weights": {c: 0 for c in cats}, **conds, "warnings": []}, ensure_ascii=False)


def _spec(name):
    spec = json.loads((EVAL / "expected" / f"{name}.json").read_text(encoding="utf-8"))
    jd = (EVAL / "jds" / f"{name}.txt").read_text(encoding="utf-8").rstrip("\n")
    return spec, jd


@pytest.mark.parametrize("name", ["eval_en_field_service_01", "eval_ar_facilities_01"])
def test_a_reference_answer_has_no_failure_in_any_group(name):
    spec, jd = _spec(name)
    r = E.score_expected(reference_answer(spec), jd, spec)
    assert r["valid_json"] and all(v == 0 for v in r["groups"].values()), r["groups"]


def _mutate(spec, jd, fn):
    obj = json.loads(reference_answer(spec))
    fn(obj)
    return E.score_expected(json.dumps(obj, ensure_ascii=False), jd, spec)["groups"]


def test_each_failure_group_is_detected_by_its_own_mutation():
    spec, jd = _spec("eval_en_field_service_01")

    def drop_item(o):                       # an omission
        o["categories"]["skills"] = [x for x in o["categories"]["skills"] if "schematics" not in x["text"]]

    def wrong_category(o):
        o["categories"]["skills"][0]["text"] = o["categories"]["skills"][0]["text"]
        o["categories"]["domain_knowledge"].append(o["categories"]["skills"].pop(0))

    def wrong_importance(o):
        o["categories"]["domain_knowledge"][0]["importance"] = "preferred"

    def partial_or(o):
        o["categories"]["certifications"][0]["alternatives"] = ["Certified welder"]

    def and_merged(o):
        o["categories"]["soft_skills"] = [{**o["categories"]["soft_skills"][0], "text": "Communication and teamwork",
                                           "source_text": "Communication and teamwork skills."}]
        o["categories"]["soft_skills"] = o["categories"]["soft_skills"][:1]

    def invented_wording(o):
        o["categories"]["skills"][0]["source_text"] = "Able to read schematics."

    def wrong_list(o):
        o["informational_items"].append(o["non_scoreable_requirements"].pop(0))

    def duty_omitted(o):
        o["categories"]["experience"].pop()

    def extra_item(o):
        o["categories"]["skills"].append({**o["categories"]["skills"][0], "text": "Basic typing", "source_text":
                                          "Familiarity with SCADA systems or PLC programming."})

    def invented_duty(o):
        o["categories"]["experience"].append({**o["categories"]["experience"][0], "text": "Make tea",
                                              "source_text": "Make tea for the team every morning."})

    cases = {
        "omissions": drop_item, "categories": wrong_category, "importance": wrong_importance, "or_and": partial_or,
        "routing": wrong_list, "invented": invented_wording, "extra_items": extra_item,
    }
    for group, fn in cases.items():
        g = _mutate(spec, jd, fn)
        assert g[group] >= 1, (group, g)
    assert _mutate(spec, jd, and_merged)["omissions"] == 1
    assert _mutate(spec, jd, duty_omitted)["omissions"] >= 1
    assert _mutate(spec, jd, invented_duty)["invented"] >= 1


def test_a_non_json_answer_fails_every_entry():
    spec, jd = _spec("eval_ar_facilities_01")
    r = E.score_expected("not json", jd, spec)
    assert r["valid_json"] is False and r["groups"]["omissions"] == len(spec["items"])


# ── 6. the plan, the budget and the dry-run ──────────────────────────────────────────────────────────────────────────

def test_the_plan_is_twenty_two_calls_under_the_cap():
    p = E.plan()
    assert p["call_count"] == 22
    by = {}
    for c in p["calls"]:
        by[(c["jd"], c["arm"])] = by.get((c["jd"], c["arm"]), 0) + 1
    assert by == {("tc20", "v2-3"): 5, ("tc20", "v2-5"): 5, ("eval_en_field_service_01", "v2-3"): 3,
                  ("eval_en_field_service_01", "v2-5"): 3, ("eval_ar_facilities_01", "v2-3"): 3, ("eval_ar_facilities_01", "v2-5"): 3}
    assert p["retries"] == 0 and p["model"] == cmp.MODEL and p["cap_usd"] == E.CAP_USD == 2.0
    assert p["worst_case_total_usd"] == pytest.approx(1.706582, abs=1e-6) and p["worst_case_total_usd"] <= 2.0
    assert p["pricing_status"] == cmp.PRICING_STATUS


def test_the_plan_refuses_a_total_above_the_cap():
    with pytest.raises(E.EvalError, match="exceeds the cap"):
        E.plan(cap=1.0)


def test_the_dry_run_makes_no_network_call_and_records_the_pins(monkeypatch, capsys):
    def boom(*_a, **_k):
        raise AssertionError("the dry-run must not reach the network")
    monkeypatch.setattr(cmp, "openai_call_model", boom)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert E.main([]) == 0
    out = json.loads(capsys.readouterr().out)
    pins = out["pins"]
    assert pins["candidate_sha256"] == E.CANDIDATE_SHA256 and pins["control_sha256"] == E.CONTROL_SHA256
    assert pins["script_sha256"] == sha(pathlib.Path(E.__file__)) and "git_commit" in pins and pins["labels_sha256"]
    assert out["pricing_verified"] is True and out["plan"]["call_count"] == 22


def test_execute_needs_an_output_directory_and_a_key(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        E.main(["--execute", "--out", str(tmp_path / "x")])
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    with pytest.raises(SystemExit):
        E.main(["--execute"])


def test_execute_refuses_when_pricing_is_not_recorded_as_verified(monkeypatch, tmp_path):
    monkeypatch.setattr(cmp, "PRICING_VERIFIED", False)
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    with pytest.raises(E.EvalError, match="not recorded as verified"):
        E.main(["--execute", "--out", str(tmp_path / "x")])


# ── 7. the protected run (fake client only) ──────────────────────────────────────────────────────────────────────────

def _client(stored_tc20_raw, fail_on=None, exc=None, model=None):
    seen = []
    refs = {}
    for name in ("eval_en_field_service_01", "eval_ar_facilities_01"):
        spec, jd = _spec(name)
        refs[jd] = reference_answer(spec)

    def call(messages):
        seen.append(messages)
        if fail_on == len(seen):
            raise exc
        user = messages[1]["content"]
        raw = next((v for k, v in refs.items() if k in user), stored_tc20_raw)
        return {"raw": raw, "finish_reason": "stop", "model": model or E.MODEL, "usage": {"prompt_tokens": 1000, "completion_tokens": 500}}
    call.seen = seen
    return call


@pytest.fixture(scope="module")
def stored_tc20_raw():
    rows = [json.loads(x) for x in (BACKEND / "tests" / "fixtures" / "requirements_v2_technical_coordinator_20" / "audit"
                                    / "run_tc20_v2-3_v2-4" / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    return next(r["raw"] for r in rows if r["arm"] == "v2-3")


def test_a_full_run_writes_the_pins_and_twenty_two_records(tmp_path, stored_tc20_raw):
    client = _client(stored_tc20_raw)
    out = tmp_path / "run"
    res = E.run(out, client)
    assert res["calls_attempted"] == 22 and res["successful_calls"] == 22 and res["stopped"] is None and len(client.seen) == 22
    man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert man["pins"]["candidate_sha256"] == E.CANDIDATE_SHA256 and man["pins"]["script_sha256"] and "git_commit" in man["pins"]
    rows = [json.loads(x) for x in (out / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 22 and all(r["model_returned"] == E.MODEL and r["input"]["user_sha256"] for r in rows)


def test_the_run_stops_at_the_first_failure_without_a_retry(tmp_path, stored_tc20_raw):
    class Bad(Exception):
        status_code = 400

    client = _client(stored_tc20_raw, fail_on=5, exc=Bad("echo of the request"))
    res = E.run(tmp_path / "run", client)
    assert len(client.seen) == 5 and res["stopped"] == "error:Bad" and res["calls_attempted"] == 5
    last = json.loads((tmp_path / "run" / "calls.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert last["error"]["kind"] == "Bad" and last["error"]["http_status"] == 400


def test_a_returned_model_that_differs_stops_the_run(tmp_path, stored_tc20_raw):
    res = E.run(tmp_path / "run", _client(stored_tc20_raw, model="gpt-4o-mini-2024-07-18"))
    assert res["stopped"] == "error:ModelMismatch" and res["calls_attempted"] == 1


def test_the_cap_guard_stops_before_a_call_that_could_exceed_the_cap(tmp_path, stored_tc20_raw):
    client = _client(stored_tc20_raw)
    original = client

    def expensive(messages):
        r = original(messages)
        r["usage"] = {"prompt_tokens": 2_000_000, "completion_tokens": 0}
        return r
    res = E.run(tmp_path / "run", expensive)
    assert res["stopped"] == "cap_guard" and res["calls_attempted"] == 1


def test_an_existing_output_directory_is_never_overwritten(tmp_path, stored_tc20_raw):
    out = tmp_path / "run"
    out.mkdir()
    (out / "calls.jsonl").write_text("earlier evidence\n", encoding="utf-8")
    client = _client(stored_tc20_raw)
    with pytest.raises(E.EvalError, match="not empty"):
        E.run(out, client)
    assert client.seen == [] and (out / "calls.jsonl").read_text(encoding="utf-8") == "earlier evidence\n"


def test_the_report_keeps_the_groups_apart(tmp_path, stored_tc20_raw):
    out = tmp_path / "run"
    E.run(out, _client(stored_tc20_raw))
    records = [json.loads(x) for x in (out / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    rep = E.report(records)
    for arm in E.ARMS:
        assert rep[arm]["eval_en_field_service_01"]["failures_total_by_group"] == {g: 0 for g in E.GROUPS}
        assert rep[arm]["tc20"]["status"] == "scored" and rep[arm]["tc20"]["scorer"].startswith("official TC20")
