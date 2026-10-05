"""Phase P2: qualifying-context merge/persistence and criteria-worker integration (offline; no API, no DB)."""
import asyncio
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from services.qualifying_context import persistence as pers
from services.qualifying_context import runner
from services.qualifying_context.schema import QCRunResult, QualifyingContext

BACKEND = Path(__file__).resolve().parent.parent
JD = "Requirements\n- Minimum 4 years of experience as a Maintenance Planner in the cement industry."
JD_SHA = hashlib.sha256(JD.encode("utf-8")).hexdigest()
NOW = "2026-10-05T10:00:00+00:00"


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def qc_result(status="ok", state="identified", contexts=("cement industry",), *, error=None, raw=None,
              jd=JD, finish="stop", ungrounded=()):
    qc = QualifyingContext(state, tuple(contexts), "analysis") if status == "ok" else None
    if status != "ok" and error is None:
        error = "upstream 503" if status == "failed_technical" else "ungrounded_context"
    return QCRunResult(status=status, qualifying_context=qc, error=error, prompt_version=runner.PROMPT_VERSION,
                       prompt_sha256=runner.PROMPT_SHA256, model="gpt-4o-mini", temperature=0.2, max_tokens=200,
                       jd_sha256=hashlib.sha256(jd.encode("utf-8")).hexdigest(), raw=raw, finish_reason=finish,
                       response_model="gpt-4o-mini-2024-07-18",
                       usage={"prompt_tokens": 2100, "completion_tokens": 20, "total_tokens": 2120},
                       ungrounded=tuple(ungrounded))


def fresh_analysis(**exp):
    return {"scoreability": {"status": "scoreable"},
            "skills": {"required": ["Python"], "preferred": []},
            "experience": {"minimum_years": 4, "relevant_roles": ["Maintenance Planner"],
                           "key_responsibilities": [], **exp},
            "education": {"minimum_level": "None", "fields_of_study": []},
            "certifications": [], "domain_knowledge": [], "other_requirements": [],
            "scoring_weights": {"skills": 40, "experience": 40, "education": 10, "certifications": 0,
                                "soft_skills": 0, "domain_knowledge": 5, "other_requirements": 5}}


ANALYSIS_QC = {"state": "identified", "contexts": ["cement industry"], "source": "analysis"}
OLD_ANALYSIS_QC = {"state": "none", "contexts": [], "source": "analysis"}
RECRUITER_QC = {"state": "identified", "contexts": ["heavy industry plants"], "source": "recruiter"}
OLD_CURRENT = {"source": "analysis", "prompt_version": "candidate_qc-1", "prompt_sha256": runner.PROMPT_SHA256,
               "model": "gpt-4o-mini", "temperature": 0.2, "max_tokens": 200, "jd_sha256": "old-jd",
               "generated_at": "2026-01-01T00:00:00+00:00"}
OLD_AUDIT = {"schema": "qc_audit_v1", "current": OLD_CURRENT,
             "latest_run": {"status": "ok", "applied": True, "not_applied_reason": None}}
RECRUITER_AUDIT = {"schema": "qc_audit_v1", "current": {"source": "recruiter", "user_id": "u1"},
                   "latest_run": None}


def existing_with(qc=None, audit=None):
    e = fresh_analysis()
    e["skills"]["required"] = ["Stale skill"]
    if qc is not None:
        e["experience"]["qualifying_context"] = copy.deepcopy(qc)
    if audit is not None:
        e["qualifying_context_audit"] = copy.deepcopy(audit)
    return e


def merge(existing, run_result, *, fresh=None, current_jd_sha=JD_SHA):
    return pers.merge_qualifying_context(existing, fresh or fresh_analysis(), run_result, generated_at=NOW,
                                         current_jd_sha256=current_jd_sha)


def stored_qc(out):
    return out.analysis["experience"].get("qualifying_context", "ABSENT")


def latest(out):
    return out.analysis["qualifying_context_audit"]["latest_run"]


def assert_no_synthesized_none(out, existing):
    prev = (existing or {}).get("experience", {}).get("qualifying_context")
    qc = out.analysis["experience"].get("qualifying_context")
    if qc is not None and qc.get("state") == "none":
        assert qc == prev, "state none must only ever be a carried-over value"


# ── merge truth table ───────────────────────────────────────────────────────

EXISTING = {
    "no_existing": None,
    "no_qc": existing_with(),
    "analysis_qc": existing_with(OLD_ANALYSIS_QC, OLD_AUDIT),
    "recruiter_qc": existing_with(RECRUITER_QC, RECRUITER_AUDIT),
}
RUNS = {
    "flag_off_or_skipped": None,
    "success": qc_result("ok"),
    "technical_failure": qc_result("failed_technical"),
    "validation_failure": qc_result("failed_validation", raw='{"state": "identified", "contexts": ["cement sector"]}',
                                    ungrounded=("cement sector",)),
}
EXPECTED = {   # (stored qualifying_context, applied, not_applied_reason, audit.current)
    ("no_existing", "flag_off_or_skipped"): ("ABSENT", None, None, "NO_AUDIT"),
    ("no_existing", "success"): (ANALYSIS_QC, True, None, "RUN"),
    ("no_existing", "technical_failure"): ("ABSENT", False, "run_failed", None),
    ("no_existing", "validation_failure"): ("ABSENT", False, "run_failed", None),
    ("no_qc", "flag_off_or_skipped"): ("ABSENT", None, None, "NO_AUDIT"),
    ("no_qc", "success"): (ANALYSIS_QC, True, None, "RUN"),
    ("no_qc", "technical_failure"): ("ABSENT", False, "run_failed", None),
    ("no_qc", "validation_failure"): ("ABSENT", False, "run_failed", None),
    ("analysis_qc", "flag_off_or_skipped"): (OLD_ANALYSIS_QC, None, None, "OLD_AUDIT"),
    ("analysis_qc", "success"): (ANALYSIS_QC, True, None, "RUN"),
    ("analysis_qc", "technical_failure"): (OLD_ANALYSIS_QC, False, "run_failed", OLD_CURRENT),
    ("analysis_qc", "validation_failure"): (OLD_ANALYSIS_QC, False, "run_failed", OLD_CURRENT),
    ("recruiter_qc", "flag_off_or_skipped"): (RECRUITER_QC, None, None, "RECRUITER_AUDIT"),
    ("recruiter_qc", "success"): (RECRUITER_QC, False, "recruiter_owned", RECRUITER_AUDIT["current"]),
    ("recruiter_qc", "technical_failure"): (RECRUITER_QC, False, "recruiter_owned", RECRUITER_AUDIT["current"]),
    ("recruiter_qc", "validation_failure"): (RECRUITER_QC, False, "recruiter_owned", RECRUITER_AUDIT["current"]),
}


class TestMergeTruthTable:
    @pytest.mark.parametrize("ek,rk", list(EXPECTED), ids=[f"{e}-{r}" for e, r in EXPECTED])
    def test_cell(self, ek, rk):
        existing, run_result = copy.deepcopy(EXISTING[ek]), RUNS[rk]
        snapshot = copy.deepcopy(existing)
        out = merge(existing, run_result)
        exp_qc, exp_applied, exp_reason, exp_current = EXPECTED[(ek, rk)]
        assert stored_qc(out) == exp_qc
        assert existing == snapshot                             # pure: inputs never mutated
        assert out.applied is bool(exp_applied) and out.not_applied_reason == exp_reason
        audit = out.analysis.get("qualifying_context_audit", "NO_AUDIT")
        if exp_current == "NO_AUDIT":
            assert audit == "NO_AUDIT"
        elif exp_current == "OLD_AUDIT":
            assert audit == OLD_AUDIT
        elif exp_current == "RECRUITER_AUDIT":
            assert audit == RECRUITER_AUDIT
        else:
            assert audit["schema"] == "qc_audit_v1"
            if exp_current == "RUN":
                assert audit["current"] == {"source": "analysis", "prompt_version": "candidate_qc-1",
                                            "prompt_sha256": runner.PROMPT_SHA256, "model": "gpt-4o-mini",
                                            "temperature": 0.2, "max_tokens": 200, "jd_sha256": JD_SHA,
                                            "generated_at": NOW}
            else:
                assert audit["current"] == exp_current
            lr = audit["latest_run"]
            assert lr["applied"] is exp_applied and lr["not_applied_reason"] == exp_reason
            assert lr["not_applied_reason"] in pers.NOT_APPLIED_REASONS
            assert lr["status"] == run_result.status and lr["jd_sha256"] == JD_SHA
        # the main (fresh) analysis always wins for every non-QC field
        assert out.analysis["skills"]["required"] == ["Python"]
        assert_no_synthesized_none(out, existing)

    def test_recruiter_object_preserved_byte_for_byte(self):
        odd = {"state": "identified", "contexts": ["x  y", "ٌZ"], "source": "recruiter", "note": "kept"}
        out = merge(existing_with(odd, RECRUITER_AUDIT), qc_result("ok"))
        assert json.dumps(stored_qc(out), ensure_ascii=False) == json.dumps(odd, ensure_ascii=False)

    def test_latest_ai_result_recorded_even_when_not_applied(self):
        out = merge(existing_with(RECRUITER_QC, RECRUITER_AUDIT), qc_result("ok"))
        assert latest(out)["result"] == ANALYSIS_QC and latest(out)["applied"] is False
        out2 = merge(existing_with(OLD_ANALYSIS_QC, OLD_AUDIT), qc_result("ok"), current_jd_sha="other")
        assert latest(out2)["result"] == ANALYSIS_QC

    def test_jd_changed_during_run(self):
        out = merge(existing_with(OLD_ANALYSIS_QC, OLD_AUDIT), qc_result("ok"), current_jd_sha="changed")
        assert stored_qc(out) == OLD_ANALYSIS_QC
        assert out.analysis["qualifying_context_audit"]["current"] == OLD_CURRENT
        assert (latest(out)["applied"], latest(out)["not_applied_reason"]) == (False, "jd_changed")
        out2 = merge(None, qc_result("ok"), current_jd_sha="changed")
        assert stored_qc(out2) == "ABSENT" and out2.analysis["qualifying_context_audit"]["current"] is None
        out3 = merge(None, qc_result("ok"), current_jd_sha=None)          # JD unreadable -> never applied
        assert stored_qc(out3) == "ABSENT" and out3.not_applied_reason == "jd_changed"

    def test_failure_with_jd_change_reports_run_failed(self):
        out = merge(None, qc_result("failed_technical"), current_jd_sha="changed")
        assert out.not_applied_reason == "run_failed"

    def test_main_llm_cannot_inject_qc_or_audit(self):
        injected = fresh_analysis(qualifying_context={"state": "none", "contexts": [], "source": "analysis"})
        injected["qualifying_context_audit"] = {"schema": "qc_audit_v1", "current": {"source": "recruiter"}}
        for existing, run_result in ((None, None), (None, qc_result("failed_validation")),
                                     (existing_with(OLD_ANALYSIS_QC, OLD_AUDIT), None)):
            out = merge(existing, run_result, fresh=injected)
            assert stored_qc(out) == (OLD_ANALYSIS_QC if existing else "ABSENT")
            audit = out.analysis.get("qualifying_context_audit")
            assert audit is None or audit.get("current", {}) in (None, OLD_CURRENT)
            assert "qualifying_context" not in out.original["experience"]
            assert out.original.get("qualifying_context_audit", {}).get("current") is None
        assert "qualifying_context" in injected["experience"]          # caller's dict not mutated

    def test_raw_rules(self):
        long_raw = "x" * 5000
        v = merge(None, qc_result("failed_validation", raw=long_raw))
        assert latest(v)["raw"] == "x" * 2000
        t = merge(None, qc_result("failed_technical", raw="partial"))
        assert latest(t)["raw"] is None
        ok = merge(None, qc_result("ok", raw=json.dumps(ANALYSIS_QC)))
        assert latest(ok)["raw"] is None
        err = merge(None, qc_result("failed_technical", error="e" * 900))
        assert len(latest(err)["error"]) == 500
        assert len(pers.technical_failure(JD, "z" * 900).error) == 500

    def test_latest_run_fields(self):
        lr = latest(merge(None, qc_result("ok")))
        assert set(lr) == {"status", "error", "prompt_version", "prompt_sha256", "model", "temperature",
                           "max_tokens", "jd_sha256", "generated_at", "finish_reason", "response_model", "usage",
                           "raw", "ungrounded", "result", "applied", "not_applied_reason"}
        assert lr["generated_at"] == NOW and lr["usage"]["total_tokens"] == 2120

    def test_never_synthesizes_none_or_uncertain(self):
        for ek, existing in EXISTING.items():
            for rk, run_result in RUNS.items():
                if run_result is not None and run_result.ok:
                    continue
                out = merge(copy.deepcopy(existing), run_result)
                prev = (existing or {}).get("experience", {}).get("qualifying_context", "ABSENT")
                assert stored_qc(out) == prev, (ek, rk)          # failures/skips never change the object

    def test_technical_failure_helper(self):
        r = pers.technical_failure(JD, "PromptIntegrityError: bad sha")
        assert r.status == "failed_technical" and r.qualifying_context is None and r.jd_sha256 == JD_SHA
        assert (r.prompt_sha256, r.model, r.temperature, r.max_tokens) == (runner.PROMPT_SHA256, "gpt-4o-mini",
                                                                           0.2, 200)


class TestOriginalBaseline:
    def test_success_includes_ai_qc_and_audit(self):
        o = merge(None, qc_result("ok")).original
        assert o["experience"]["qualifying_context"] == ANALYSIS_QC
        assert o["qualifying_context_audit"]["current"]["jd_sha256"] == JD_SHA
        assert o["qualifying_context_audit"]["latest_run"]["applied"] is True

    def test_failure_has_no_qc_but_failed_audit(self):
        o = merge(None, qc_result("failed_technical")).original
        assert "qualifying_context" not in o["experience"]
        assert o["qualifying_context_audit"]["current"] is None
        assert o["qualifying_context_audit"]["latest_run"]["status"] == "failed_technical"

    @pytest.mark.parametrize("rk", list(RUNS))
    def test_never_captures_recruiter_qc(self, rk):
        out = merge(existing_with(RECRUITER_QC, RECRUITER_AUDIT), RUNS[rk])
        dumped = json.dumps(out.original, ensure_ascii=False)
        assert "recruiter" not in dumped and "heavy industry plants" not in dumped
        assert stored_qc(out) == RECRUITER_QC

    def test_never_captures_previous_state(self):
        o = merge(existing_with(OLD_ANALYSIS_QC, OLD_AUDIT), None).original
        assert "qualifying_context" not in o["experience"] and "qualifying_context_audit" not in o
        assert o["skills"]["required"] == ["Python"]


# ── worker integration (fake DB session; patched model calls) ───────────────

class _Result:
    def __init__(self, row=None, scalar=None):
        self._row, self._scalar = row, scalar

    def mappings(self):
        return self

    def first(self):
        return self._row

    def scalar_one_or_none(self):
        return self._scalar


class DB:
    """Shared state for every FakeSession of one worker run."""

    def __init__(self, *, flag="__missing__", flag_error=False, stored=None, current_description=JD):
        self.flag, self.flag_error = flag, flag_error
        self.stored, self.current_description = stored, current_description
        self.statements, self.session_log = [], []

    def session(self):
        return FakeSession(self)

    def updates(self):
        return [p for sql, p in self.statements if "analysis_json              = CAST(:aj" in sql]


class FakeSession:
    def __init__(self, db):
        self.db, self.log = db, []
        db.session_log.append(self.log)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.db.statements.append((sql, params))
        self.log.append(sql)
        if "FROM system_config" in sql:
            if self.db.flag_error:
                raise RuntimeError("db down")
            return _Result(scalar=None if self.db.flag == "__missing__" else self.db.flag)
        if "FROM job_criteria jc" in sql:
            return _Result(row={"analysis_json": copy.deepcopy(self.db.stored),
                                "description": self.db.current_description})
        return _Result()

    async def commit(self):
        self.log.append("COMMIT")

    async def rollback(self):
        self.log.append("ROLLBACK")


@pytest.fixture
def worker(monkeypatch):
    import sqlalchemy.ext.asyncio as sa_async
    import services.ai_service as ai
    from workers import criteria_worker as cw

    state = {"main": fresh_analysis(), "qc": qc_result("ok"), "qc_calls": [], "main_calls": 0}

    async def fake_prompt(db, code):
        return None

    async def fake_extract(description, **kw):
        state["main_calls"] += 1
        if isinstance(state["main"], Exception):
            raise state["main"]
        return copy.deepcopy(state["main"])

    async def fake_qc(jd_text, *, client=None):
        state["qc_calls"].append(jd_text)
        if isinstance(state["qc"], BaseException):
            raise state["qc"]
        return state["qc"]

    def no_registry_engine(*a, **k):
        raise RuntimeError("registry disabled in tests")

    monkeypatch.setattr(ai, "load_active_prompt", fake_prompt)
    monkeypatch.setattr(ai, "extract_job_criteria", fake_extract)
    monkeypatch.setattr(runner, "run_qualifying_context", fake_qc)
    # other tests may stub sqlalchemy.ext.asyncio in sys.modules; the worker swallows registry errors anyway
    monkeypatch.setattr(sa_async, "create_async_engine", no_registry_engine, raising=False)

    def go(db):
        run(cw._extract_async("job-1", JD, db.session, None))
        return db
    state["go"] = go
    return state


def written(db):
    (params,) = db.updates()
    return json.loads(params["aj"]), json.loads(params["orig"]), params


class TestWorker:
    def test_flag_missing_means_off_and_runner_not_called(self, worker):
        db = worker["go"](DB())
        aj, orig, params = written(db)
        assert worker["qc_calls"] == [] and "qualifying_context" not in aj["experience"]
        assert "qualifying_context_audit" not in aj and params["status"] == "completed"

    @pytest.mark.parametrize("flag", ["false", "TRUE ", " True", "yes", "1", "", "on"])
    def test_only_exact_true_enables(self, worker, flag):
        worker["go"](DB(flag=flag))
        expected = 1 if flag.strip().lower() == "true" else 0
        assert len(worker["qc_calls"]) == expected

    def test_flag_db_error_means_off_and_main_prompt_unaffected(self, worker):
        db = worker["go"](DB(flag="true", flag_error=True))
        assert worker["qc_calls"] == [] and worker["main_calls"] == 1
        assert "ROLLBACK" in db.session_log[0]
        assert written(db)[2]["status"] == "completed"

    def test_flag_off_preserves_existing_analysis_qc(self, worker):
        stored = existing_with(OLD_ANALYSIS_QC, OLD_AUDIT)
        aj, _, _ = written(worker["go"](DB(stored=stored)))
        assert aj["experience"]["qualifying_context"] == OLD_ANALYSIS_QC
        assert aj["qualifying_context_audit"] == OLD_AUDIT and worker["qc_calls"] == []

    def test_flag_off_preserves_recruiter_qc(self, worker):
        stored = existing_with(RECRUITER_QC, RECRUITER_AUDIT)
        aj, orig, _ = written(worker["go"](DB(stored=stored)))
        assert aj["experience"]["qualifying_context"] == RECRUITER_QC
        assert "recruiter" not in json.dumps(orig)

    def test_flag_on_success(self, worker):
        db = worker["go"](DB(flag="true"))
        aj, orig, params = written(db)
        assert worker["qc_calls"] == [JD]
        assert aj["experience"]["qualifying_context"] == ANALYSIS_QC
        assert orig["experience"]["qualifying_context"] == ANALYSIS_QC
        assert aj["qualifying_context_audit"]["current"]["jd_sha256"] == JD_SHA
        assert params["status"] == "completed"

    def test_insufficient_skips_qc(self, worker):
        worker["main"] = {**fresh_analysis(), "scoreability": {"status": "insufficient", "reason": "thin"}}
        db = worker["go"](DB(flag="true", stored=existing_with(OLD_ANALYSIS_QC, OLD_AUDIT)))
        aj, _, params = written(db)
        assert worker["qc_calls"] == [] and params["status"] == "insufficient"
        assert aj["experience"]["qualifying_context"] == OLD_ANALYSIS_QC

    @pytest.mark.parametrize("failure", ["technical", "validation", "prompt_integrity", "arbitrary"])
    def test_qc_failure_still_saves_completed_main_analysis(self, worker, failure):
        worker["qc"] = {"technical": qc_result("failed_technical"),
                        "validation": qc_result("failed_validation", raw="{bad"),
                        "prompt_integrity": runner.PromptIntegrityError("sha mismatch"),
                        "arbitrary": KeyError("boom")}[failure]
        db = worker["go"](DB(flag="true", stored=existing_with(OLD_ANALYSIS_QC, OLD_AUDIT)))   # no exception
        aj, _, params = written(db)
        assert params["status"] == "completed" and aj["skills"]["required"] == ["Python"]
        assert aj["experience"]["qualifying_context"] == OLD_ANALYSIS_QC
        lr = aj["qualifying_context_audit"]["latest_run"]
        assert lr["applied"] is False and lr["not_applied_reason"] == "run_failed"
        assert lr["status"] == ("failed_validation" if failure == "validation" else "failed_technical")
        if failure == "prompt_integrity":
            assert lr["error"].startswith("PromptIntegrityError")
        assert worker["main_calls"] == 1

    def test_main_extraction_failure_propagates_and_qc_not_called(self, worker):
        worker["main"] = RuntimeError("main LLM down")
        db = DB(flag="true")
        with pytest.raises(RuntimeError, match="main LLM down"):
            worker["go"](db)
        assert worker["qc_calls"] == [] and db.updates() == []

    def test_recruiter_edit_during_run_wins(self, worker):
        # the stored value read BEFORE the model call had no QC; the recruiter wrote one while the model ran
        db = DB(flag="true", stored=existing_with())

        async def qc_then_recruiter_edits(jd_text, *, client=None):
            worker["qc_calls"].append(jd_text)
            db.stored = existing_with(RECRUITER_QC, RECRUITER_AUDIT)
            return qc_result("ok")
        import services.qualifying_context.runner as r
        orig_fn = r.run_qualifying_context
        r.run_qualifying_context = qc_then_recruiter_edits
        try:
            worker["go"](db)
        finally:
            r.run_qualifying_context = orig_fn
        aj, orig, _ = written(db)
        assert aj["experience"]["qualifying_context"] == RECRUITER_QC
        assert aj["qualifying_context_audit"]["latest_run"]["not_applied_reason"] == "recruiter_owned"
        assert aj["qualifying_context_audit"]["latest_run"]["result"] == ANALYSIS_QC
        assert "recruiter" not in json.dumps(orig)

    def test_jd_changed_during_call(self, worker):
        db = worker["go"](DB(flag="true", stored=existing_with(OLD_ANALYSIS_QC, OLD_AUDIT),
                             current_description=JD + " Updated."))
        aj, _, _ = written(db)
        assert aj["experience"]["qualifying_context"] == OLD_ANALYSIS_QC
        assert aj["qualifying_context_audit"]["latest_run"]["not_applied_reason"] == "jd_changed"

    def test_main_llm_injection_never_persisted(self, worker):
        worker["main"] = fresh_analysis(qualifying_context={"state": "none", "contexts": [], "source": "analysis"})
        worker["main"]["qualifying_context_audit"] = {"schema": "qc_audit_v1"}
        aj, orig, _ = written(worker["go"](DB()))
        assert "qualifying_context" not in aj["experience"] and "qualifying_context_audit" not in aj
        assert "qualifying_context" not in orig["experience"]

    def test_persistence_select_locks_row_before_update_in_one_transaction(self, worker):
        db = worker["go"](DB(flag="true"))
        final = db.session_log[-1]
        lock = next(i for i, s in enumerate(final) if "FOR UPDATE OF jc" in s)
        upd = next(i for i, s in enumerate(final) if "analysis_json              = CAST(:aj" in s)
        assert lock < upd < final.index("COMMIT") and final.count("COMMIT") == 1
        assert "JOIN jobs j" in final[lock] and "SELECT jc.analysis_json, j.description" in final[lock]
        assert "COALESCE(original_analysis_json, CAST(:orig AS jsonb))" in final[upd]

    def test_model_call_happens_outside_any_transaction(self, worker):
        db = DB(flag="true")
        seen = {}

        async def probe(jd_text, *, client=None):
            seen["logs"] = [list(lg) for lg in db.session_log]
            return qc_result("ok")
        import services.qualifying_context.runner as r
        orig_fn = r.run_qualifying_context
        r.run_qualifying_context = probe
        try:
            worker["go"](db)
        finally:
            r.run_qualifying_context = orig_fn
        assert len(seen["logs"]) == 1 and seen["logs"][0][-1] != "FOR UPDATE"   # only session 1 existed
        assert not any("FOR UPDATE" in s for s in seen["logs"][0])

    def test_string_jsonb_value_is_read(self, worker):
        stored = json.dumps(existing_with(RECRUITER_QC, RECRUITER_AUDIT))
        aj, _, _ = written(worker["go"](DB(flag="true", stored=stored)))
        assert aj["experience"]["qualifying_context"] == RECRUITER_QC


# ── production scoring is unaffected ────────────────────────────────────────

def with_qc_keys(analysis):
    a = copy.deepcopy(analysis)
    a["experience"]["qualifying_context"] = ANALYSIS_QC
    a["qualifying_context_audit"] = {"schema": "qc_audit_v1", "current": OLD_CURRENT,
                                     "latest_run": {"raw": "{bad", "status": "failed_validation"}}
    return a


class TestScoringUnchanged:
    def test_flatten_criteria_for_scoring(self):
        from services.ai_service import flatten_criteria_for_scoring
        a = fresh_analysis()
        assert flatten_criteria_for_scoring(with_qc_keys(a)) == flatten_criteria_for_scoring(a)

    def test_llm_criteria_mapper_flattening(self):
        from services.llm_criteria_mapper import _flatten_criteria
        a = fresh_analysis()
        assert _flatten_criteria(with_qc_keys(a)) == _flatten_criteria(a)

    def test_criteria_match_engine_decisions(self):
        spec = importlib.util.spec_from_file_location("tcm", BACKEND / "tests" / "test_criteria_matcher.py")
        tcm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tcm)
        from services.criteria_matcher import CriteriaMatchEngine
        facts = tcm._make_cv_facts(skills=["Python"], experience_years=5.0)
        crit = tcm._make_criteria(required_skills=["Python", "SQL"], min_years=4)
        a = CriteriaMatchEngine().match(facts, crit, "app", "job")
        b = CriteriaMatchEngine().match(facts, with_qc_keys(crit), "app", "job")
        da, db_ = tcm.dataclasses.asdict(a), tcm.dataclasses.asdict(b)
        assert da.pop("criteria_version") != db_.pop("criteria_version")        # trace hash only
        for d in (da, db_):
            d.pop("matched_at", None)
            d.pop("created_at", None)
        assert da == db_

    def test_validate_criteria_never_defaults_qualifying_context(self):
        from services.ai_service import _validate_criteria
        data = {"experience": {}}
        _validate_criteria(data)
        assert "qualifying_context" not in data["experience"] and "qualifying_context_audit" not in data
        data2 = {}
        _validate_criteria(data2)
        assert "qualifying_context" not in data2["experience"]


class TestFrozenArtifacts:
    PINS = {"scripts/qc_eval_fixtures/prompts/candidate_qc-1.txt":
                "fc979dd4a48b2aa5922c87f62e613700f898da13599e43e62a4fbc1304c851df",
            "services/qualifying_context/prompts/qc-1.txt":
                "fc979dd4a48b2aa5922c87f62e613700f898da13599e43e62a4fbc1304c851df",
            "scripts/qc_eval_fixtures/qc_main_cases.json":
                "c5fcd5024d9673217d0b1e8b8413d4628025932b86fcb97f56b96fc57f98317c",
            "scripts/qc_eval_fixtures/qc_heldout_cases.json":
                "d94a39f343e0185cf9d0cca664d1c744f52d5b66b6b32ac57edee22ed3a1b91f"}

    @pytest.mark.parametrize("rel", list(PINS))
    def test_byte_identical(self, rel):
        assert hashlib.sha256((BACKEND / rel).read_bytes()).hexdigest() == self.PINS[rel]
