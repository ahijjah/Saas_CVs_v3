"""Phase P3: recruiter review / confirm / edit of qualifying context (offline; no API, no DB, no model call)."""
import ast
import asyncio
import copy
import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.qualifying_context import persistence as pers
from services.qualifying_context import recruiter as rec
from services.qualifying_context import runner
from services.qualifying_context.schema import QCRunResult, QualifyingContext

BACKEND = Path(__file__).resolve().parent.parent
NOW = "2026-10-06T09:00:00+00:00"
JD = "Requirements\n- Minimum 4 years of experience as a Maintenance Planner in the cement industry."
JD_SHA = hashlib.sha256(JD.encode()).hexdigest()


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def user(role="hr_manager", tenant="t-1", uid="u-1", name="Layla Haddad"):
    return SimpleNamespace(user_id=uid, tenant_id=tenant, email="layla@example.com", role=role, full_name=name)


AI_IDENTIFIED = {"state": "identified", "contexts": ["cement industry"], "source": "analysis"}
AI_NONE = {"state": "none", "contexts": [], "source": "analysis"}
AI_UNCERTAIN = {"state": "uncertain", "contexts": ["regulated plants"], "source": "analysis"}
AI_CURRENT = {"source": "analysis", "prompt_version": "candidate_qc-1", "prompt_sha256": runner.PROMPT_SHA256,
              "model": "gpt-4o-mini", "temperature": 0.2, "max_tokens": 200, "jd_sha256": JD_SHA,
              "generated_at": "2026-10-05T10:00:00+00:00"}
LATEST_RUN = {"status": "ok", "error": None, "prompt_version": "candidate_qc-1", "prompt_sha256": runner.PROMPT_SHA256,
              "model": "gpt-4o-mini", "temperature": 0.2, "max_tokens": 200, "jd_sha256": JD_SHA,
              "generated_at": "2026-10-05T10:00:00+00:00", "finish_reason": "stop",
              "response_model": "gpt-4o-mini-2024-07-18", "usage": {"total_tokens": 2120}, "raw": None,
              "ungrounded": [], "result": AI_IDENTIFIED, "applied": True, "not_applied_reason": None}


def analysis(qc=None, current=AI_CURRENT, latest=LATEST_RUN, audit=True):
    a = {"skills": {"required": ["Python"], "preferred": []},
         "experience": {"minimum_years": 4, "relevant_roles": ["Maintenance Planner"], "key_responsibilities": []},
         "education": {"minimum_level": "None", "fields_of_study": []}, "certifications": [],
         "domain_knowledge": [], "other_requirements": [],
         "scoring_weights": {"skills": 50, "experience": 50, "education": 0, "certifications": 0,
                             "soft_skills": 0, "domain_knowledge": 0, "other_requirements": 0}}
    if qc is not None:
        a["experience"]["qualifying_context"] = copy.deepcopy(qc)
    if audit:
        a["qualifying_context_audit"] = {"schema": "qc_audit_v1", "current": copy.deepcopy(current),
                                         "latest_run": copy.deepcopy(latest)}
    return a


# ── pure transitions ────────────────────────────────────────────────────────

class TestConfirm:
    @pytest.mark.parametrize("ai", [AI_IDENTIFIED, AI_NONE], ids=["identified", "none"])
    def test_confirm_copies_exactly(self, ai):
        a = analysis(ai)
        t = rec.build_confirm(a, ai, user(), NOW)
        stored = t.analysis["experience"]["qualifying_context"]
        assert stored == {"state": ai["state"], "contexts": ai["contexts"], "source": "recruiter"}
        assert set(stored) == {"state", "contexts", "source"}                    # frozen three keys
        cur = t.analysis["qualifying_context_audit"]["current"]
        assert cur == {"source": "recruiter", "provenance": "recruiter_confirmed", "user_id": "u-1",
                       "user_name": "Layla Haddad", "changed_at": NOW, "previous": AI_CURRENT,
                       "previous_qualifying_context": ai}
        assert t.action == "qualifying_context_confirmed" and t.previous_current == AI_CURRENT
        assert a["experience"]["qualifying_context"] == ai                         # input not mutated

    @pytest.mark.parametrize("stored,code", [
        (AI_UNCERTAIN, "nothing_to_confirm"),
        (None, "nothing_to_confirm"),
        ({"state": "identified", "contexts": ["x"], "source": "recruiter"}, "nothing_to_confirm"),
    ], ids=["uncertain", "missing", "recruiter_owned"])
    def test_reject_unconfirmable(self, stored, code):
        a = analysis(stored)
        with pytest.raises(rec.QCRecruiterError) as e:
            rec.build_confirm(a, stored, user(), NOW)
        assert (e.value.http_status, e.value.code) == (409, code)
        assert e.value.detail()["qualifying_context"] == stored

    def test_reject_changed_value(self):
        with pytest.raises(rec.QCRecruiterError) as e:
            rec.build_confirm(analysis(AI_IDENTIFIED), AI_NONE, user(), NOW)
        assert (e.value.http_status, e.value.code) == (409, "qualifying_context_changed")
        d = e.value.detail()
        assert d["qualifying_context"] == AI_IDENTIFIED
        assert d["qualifying_context_review"]["status"] == "awaiting_confirmation"

    def test_confirm_against_missing_when_client_saw_a_value(self):
        with pytest.raises(rec.QCRecruiterError) as e:
            rec.build_confirm(analysis(None), AI_IDENTIFIED, user(), NOW)
        assert e.value.code == "qualifying_context_changed"


class TestEdit:
    def test_edit_identified_preserves_recruiter_wording(self):
        words = ["  heavy-industry plants ", "Cement & Lime (GCC)", "مصانع الإسمنت"]
        t = rec.build_edit(analysis(AI_UNCERTAIN), "identified", words, AI_UNCERTAIN, user(), NOW)
        assert t.analysis["experience"]["qualifying_context"] == {
            "state": "identified", "contexts": ["heavy-industry plants", "Cement & Lime (GCC)", "مصانع الإسمنت"],
            "source": "recruiter"}
        assert t.analysis["qualifying_context_audit"]["current"]["provenance"] == "recruiter_edited"

    def test_edit_none(self):
        t = rec.build_edit(analysis(AI_IDENTIFIED), "none", [], AI_IDENTIFIED, user(), NOW)
        assert t.analysis["experience"]["qualifying_context"] == {"state": "none", "contexts": [],
                                                                  "source": "recruiter"}

    def test_edit_equal_to_ai_is_still_edited(self):
        t = rec.build_edit(analysis(AI_IDENTIFIED), "identified", ["cement industry"], AI_IDENTIFIED, user(), NOW)
        assert t.analysis["qualifying_context_audit"]["current"]["provenance"] == "recruiter_edited"
        assert t.action == "qualifying_context_edited"

    @pytest.mark.parametrize("stored", [AI_IDENTIFIED, {"state": "none", "contexts": [], "source": "recruiter"},
                                        None], ids=["over_ai", "over_recruiter", "over_missing"])
    def test_edit_over_any_state(self, stored):
        t = rec.build_edit(analysis(stored), "identified", ["listed companies"], stored, user(), NOW)
        assert t.analysis["experience"]["qualifying_context"]["contexts"] == ["listed companies"]
        assert t.previous_qc == stored

    def test_edit_without_any_existing_audit_creates_current_only(self):
        a = analysis(None, audit=False)
        t = rec.build_edit(a, "none", [], None, user(), NOW)
        audit = t.analysis["qualifying_context_audit"]
        assert set(audit) == {"schema", "current"} and "latest_run" not in audit
        assert audit["current"]["previous"] is None and audit["current"]["previous_qualifying_context"] is None

    @pytest.mark.parametrize("state,contexts", [
        ("identified", []), ("none", ["cement industry"]), ("uncertain", ["x"]), ("uncertain", []),
        ("maybe", []), (None, []), ("identified", "cement"), ("identified", [7]), ("identified", ["  "]),
        ("identified", [""]), ("identified", ["a", "a"]), ("identified", ["a", " a "]),
        ("identified", ["bad\u0007bell"]), ("identified", ["new\nline"]), ("identified", ["tab\there"]),
        ("identified", [f"c{i}" for i in range(11)]), ("identified", ["x" * 201]),
    ])
    def test_rejected(self, state, contexts):
        with pytest.raises(rec.QCRecruiterError) as e:
            rec.build_edit(analysis(AI_IDENTIFIED), state, contexts, AI_IDENTIFIED, user(), NOW)
        assert (e.value.http_status, e.value.code) == (422, "invalid_qualifying_context")

    def test_limits_are_inclusive(self):
        ten = [f"context {i}" for i in range(10)]
        assert rec.validate_edit("identified", ten)["contexts"] == ten
        assert rec.validate_edit("identified", ["y" * 200])["contexts"] == ["y" * 200]
        assert rec.validate_edit("identified", ["  " + "z" * 200 + "  "])["contexts"] == ["z" * 200]

    def test_case_variants_are_not_duplicates(self):
        assert rec.validate_edit("identified", ["Banks", "banks"])["contexts"] == ["Banks", "banks"]

    def test_expected_mismatch(self):
        with pytest.raises(rec.QCRecruiterError) as e:
            rec.build_edit(analysis(AI_IDENTIFIED), "none", [], None, user(), NOW)
        assert e.value.code == "qualifying_context_changed"


class TestAuditCurrentAndLatestRun:
    def test_latest_run_byte_identical_after_every_action(self):
        before = json.dumps(LATEST_RUN, ensure_ascii=False, sort_keys=False)
        a = analysis(AI_IDENTIFIED)
        t1 = rec.build_confirm(a, AI_IDENTIFIED, user(), NOW)
        t2 = rec.build_edit(t1.analysis, "none", [], t1.new_qc, user(uid="u-2", name="Omar"), NOW)
        t3 = rec.build_edit(t2.analysis, "identified", ["kilns"], t2.new_qc, user(), NOW)
        for t in (t1, t2, t3):
            assert json.dumps(t.analysis["qualifying_context_audit"]["latest_run"], ensure_ascii=False) == before

    def test_previous_is_one_level_only(self):
        t1 = rec.build_confirm(analysis(AI_IDENTIFIED), AI_IDENTIFIED, user(), NOW)
        t2 = rec.build_edit(t1.analysis, "none", [], t1.new_qc, user(uid="u-2"), NOW)
        t3 = rec.build_edit(t2.analysis, "identified", ["kilns"], t2.new_qc, user(uid="u-3"), NOW)
        cur = t3.analysis["qualifying_context_audit"]["current"]
        assert cur["previous"]["user_id"] == "u-2" and "previous" not in cur["previous"]
        assert cur["previous_qualifying_context"] == {"state": "none", "contexts": [], "source": "recruiter"}

    def test_other_analysis_keys_untouched(self):
        a = analysis(AI_IDENTIFIED)
        t = rec.build_confirm(a, AI_IDENTIFIED, user(), NOW)
        for k in ("skills", "education", "scoring_weights", "certifications"):
            assert t.analysis[k] == a[k]
        assert {k: v for k, v in t.analysis["experience"].items() if k != "qualifying_context"} == \
            {k: v for k, v in a["experience"].items() if k != "qualifying_context"}


# ── review status ───────────────────────────────────────────────────────────

class TestReviewStatus:
    def test_not_assessed(self):
        for a in (analysis(None, audit=False), {}, None, "garbage", {"experience": None}):
            r = rec.review_status(a)
            assert r["status"] == "not_assessed" and r["state"] is None and r["can_confirm"] is False

    @pytest.mark.parametrize("latest", [
        {**LATEST_RUN, "status": "failed_technical", "result": None, "applied": False,
         "not_applied_reason": "run_failed"},
        {**LATEST_RUN, "status": "failed_validation", "result": None, "applied": False,
         "not_applied_reason": "run_failed"},
        {**LATEST_RUN, "applied": False, "not_applied_reason": "jd_changed"},
    ])
    def test_assessment_failed(self, latest):
        r = rec.review_status(analysis(None, current=None, latest=latest))
        assert r["status"] == "assessment_failed" and r["state"] is None and r["can_confirm"] is False

    def test_needs_confirmation(self):
        r = rec.review_status(analysis(AI_UNCERTAIN))
        assert (r["status"], r["can_confirm"], r["state"], r["contexts"]) == (
            "needs_confirmation", False, "uncertain", ["regulated plants"])

    @pytest.mark.parametrize("ai", [AI_IDENTIFIED, AI_NONE])
    def test_awaiting_confirmation(self, ai):
        r = rec.review_status(analysis(ai))
        assert (r["status"], r["can_confirm"], r["state"]) == ("awaiting_confirmation", True, ai["state"])

    def test_confirmed_and_edited(self):
        t = rec.build_confirm(analysis(AI_IDENTIFIED), AI_IDENTIFIED, user(), NOW)
        r = rec.review_status(t.analysis)
        assert (r["status"], r["can_confirm"], r["changed_by_name"], r["changed_at"]) == (
            "confirmed", False, "Layla Haddad", NOW)
        t2 = rec.build_edit(t.analysis, "none", [], t.new_qc, user(name="Omar"), NOW)
        r2 = rec.review_status(t2.analysis)
        assert (r2["status"], r2["state"], r2["changed_by_name"]) == ("edited", "none", "Omar")

    def test_missing_is_never_none(self):
        for a in (analysis(None, audit=False), analysis(None, current=None,
                                                       latest={**LATEST_RUN, "status": "failed_technical"})):
            assert rec.review_status(a)["state"] is None


# ── permissions ─────────────────────────────────────────────────────────────

class TestPermissions:
    @pytest.mark.parametrize("role", ["admin", "hr_manager", "Admin", "HR_MANAGER"])
    def test_allowed(self, role):
        rec.ensure_can_edit(role)

    @pytest.mark.parametrize("role", ["super_admin", "recruiter", "viewer", "", None, "account_manager"])
    def test_rejected(self, role):
        with pytest.raises(rec.QCRecruiterError) as e:
            rec.ensure_can_edit(role)
        assert e.value.http_status == 403


# ── DB operations through a fake session ────────────────────────────────────

class _Result:
    def __init__(self, row=None):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class Store:
    """One job_criteria row per (job, tenant); a fake session replays statements against it."""

    def __init__(self, stored, *, tenant="t-1", job="job-1", audit_fails=False):
        self.rows = {(job, tenant): {"analysis_json": copy.deepcopy(stored),
                                     "original_analysis_json": {"original": True}}}
        self.audit_fails, self.statements, self.events, self.audit_rows = audit_fails, [], [], []

    def session(self):
        return FakeSession(self)


class FakeSession:
    def __init__(self, store):
        self.store, self.pending = store, {}

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.store.statements.append((sql, params))
        self.store.events.append(sql[:40])
        if "FOR UPDATE OF jc" in sql:
            row = self.store.rows.get((params["jid"], params["tid"]))
            self.pending["key"] = (params["jid"], params["tid"])
            return _Result(None if row is None else {"analysis_json": copy.deepcopy(row["analysis_json"])})
        if sql.startswith("UPDATE job_criteria"):
            self.pending["aj"] = json.loads(params["aj"])
            return _Result()
        if "INSERT INTO audit_logs" in sql:
            if self.store.audit_fails:
                raise RuntimeError("audit insert failed")
            self.pending["audit"] = params
            return _Result()
        return _Result()

    async def commit(self):
        self.store.events.append("COMMIT")
        if "aj" in self.pending:
            self.store.rows[self.pending["key"]]["analysis_json"] = self.pending["aj"]
        if "audit" in self.pending:
            self.store.audit_rows.append(self.pending["audit"])
        self.pending = {}

    async def rollback(self):
        self.store.events.append("ROLLBACK")
        self.pending = {}


def stored(store, job="job-1", tenant="t-1"):
    return store.rows[(job, tenant)]["analysis_json"]


@pytest.fixture(autouse=True)
def no_model_calls(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("P3 must never call the model")
    monkeypatch.setattr(runner, "run_qualifying_context", boom)


class TestService:
    def test_confirm_commits_with_audit(self):
        s = Store(analysis(AI_IDENTIFIED))
        out = run(rec.confirm_qualifying_context(s.session(), user(), "job-1", AI_IDENTIFIED))
        assert out["qualifying_context"] == {"state": "identified", "contexts": ["cement industry"],
                                             "source": "recruiter"}
        assert out["qualifying_context_review"]["status"] == "confirmed"
        assert stored(s)["experience"]["qualifying_context"]["source"] == "recruiter"
        (audit,) = s.audit_rows
        assert (audit["action"], audit["resource_type"], audit["resource_id"], audit["tenant_id"],
                audit["user_id"]) == ("qualifying_context_confirmed", "job", "job-1", "t-1", "u-1")
        assert json.loads(audit["details"]) == {"job_id": "job-1", "previous_qualifying_context": AI_IDENTIFIED,
                                                "new_qualifying_context": out["qualifying_context"],
                                                "previous_current": AI_CURRENT}

    def test_edit_commits_with_audit(self):
        s = Store(analysis(AI_UNCERTAIN))
        run(rec.edit_qualifying_context(s.session(), user(), "job-1", "none", [], AI_UNCERTAIN))
        assert stored(s)["experience"]["qualifying_context"] == {"state": "none", "contexts": [],
                                                                 "source": "recruiter"}
        assert s.audit_rows[0]["action"] == "qualifying_context_edited"

    def test_lock_update_audit_commit_order_in_one_transaction(self):
        s = Store(analysis(AI_IDENTIFIED))
        run(rec.confirm_qualifying_context(s.session(), user(), "job-1", AI_IDENTIFIED))
        ev = s.events
        lock = next(i for i, e in enumerate(ev) if e.startswith("SELECT jc.analysis_json"))
        upd = next(i for i, e in enumerate(ev) if e.startswith("UPDATE job_criteria"))
        aud = next(i for i, e in enumerate(ev) if e.startswith("INSERT INTO audit_logs"))
        assert lock < upd < aud < ev.index("COMMIT") and ev.count("COMMIT") == 1
        assert "FOR UPDATE OF jc" in s.statements[[i for i, _ in enumerate(s.statements)][lock]][0]

    def test_tenant_isolation(self):
        s = Store(analysis(AI_IDENTIFIED), tenant="t-1")
        with pytest.raises(rec.QCRecruiterError) as e:
            run(rec.confirm_qualifying_context(s.session(), user(tenant="t-2"), "job-1", AI_IDENTIFIED))
        assert e.value.http_status == 404
        lock_sql, params = next((q, p) for q, p in s.statements if "FOR UPDATE OF jc" in q)
        assert "j.tenant_id = :tid" in lock_sql and params == {"jid": "job-1", "tid": "t-2"}
        assert "COMMIT" not in s.events and stored(s)["experience"]["qualifying_context"] == AI_IDENTIFIED

    @pytest.mark.parametrize("role", ["super_admin", "recruiter", "viewer"])
    def test_forbidden_roles_touch_nothing(self, role):
        s = Store(analysis(AI_IDENTIFIED))
        with pytest.raises(rec.QCRecruiterError) as e:
            run(rec.confirm_qualifying_context(s.session(), user(role=role), "job-1", AI_IDENTIFIED))
        assert e.value.http_status == 403 and s.statements == []

    def test_invalid_edit_rejected_before_lock(self):
        s = Store(analysis(AI_IDENTIFIED))
        with pytest.raises(rec.QCRecruiterError) as e:
            run(rec.edit_qualifying_context(s.session(), user(), "job-1", "uncertain", ["x"], AI_IDENTIFIED))
        assert e.value.http_status == 422 and s.statements == []

    def test_conflict_rolls_back_and_returns_current(self):
        s = Store(analysis(AI_NONE))
        with pytest.raises(rec.QCRecruiterError) as e:
            run(rec.confirm_qualifying_context(s.session(), user(), "job-1", AI_IDENTIFIED))
        assert e.value.code == "qualifying_context_changed" and e.value.detail()["qualifying_context"] == AI_NONE
        assert "ROLLBACK" in s.events and "COMMIT" not in s.events
        assert not any(q.startswith("UPDATE") for q, _ in s.statements)

    def test_two_recruiter_actions_same_expected_second_conflicts(self):
        s = Store(analysis(AI_IDENTIFIED))
        run(rec.confirm_qualifying_context(s.session(), user(uid="u-1"), "job-1", AI_IDENTIFIED))
        with pytest.raises(rec.QCRecruiterError) as e:
            run(rec.edit_qualifying_context(s.session(), user(uid="u-2"), "job-1", "none", [], AI_IDENTIFIED))
        assert e.value.code == "qualifying_context_changed"
        assert stored(s)["qualifying_context_audit"]["current"]["user_id"] == "u-1"
        assert len(s.audit_rows) == 1

    def test_audit_failure_prevents_commit(self):
        s = Store(analysis(AI_IDENTIFIED), audit_fails=True)
        with pytest.raises(RuntimeError, match="audit insert failed"):
            run(rec.confirm_qualifying_context(s.session(), user(), "job-1", AI_IDENTIFIED))
        assert "COMMIT" not in s.events and "ROLLBACK" in s.events
        assert stored(s)["experience"]["qualifying_context"] == AI_IDENTIFIED and s.audit_rows == []

    def test_log_action_default_swallows_but_strict_raises(self):
        # documents the REAL helper behaviour P3 relies on
        from services.audit_service import log_action
        s = Store(analysis(AI_IDENTIFIED), audit_fails=True)
        run(log_action(s.session(), "t-1", "u-1", None, "x", "job", "job-1", {"a": 1}))       # swallowed
        with pytest.raises(RuntimeError):
            run(log_action(s.session(), "t-1", "u-1", None, "x", "job", "job-1", {"a": 1}, strict=True))

    def test_original_analysis_json_never_touched(self):
        s = Store(analysis(AI_IDENTIFIED))
        run(rec.confirm_qualifying_context(s.session(), user(), "job-1", AI_IDENTIFIED))
        run(rec.edit_qualifying_context(s.session(), user(), "job-1", "none", [],
                                        stored(s)["experience"]["qualifying_context"]))
        assert all("original_analysis_json" not in q for q, _ in s.statements)
        assert "original_analysis_json" not in rec.LOCK_SQL + rec.UPDATE_SQL
        assert s.rows[("job-1", "t-1")]["original_analysis_json"] == {"original": True}

    def test_string_jsonb_is_read(self):
        s = Store(None)
        s.rows[("job-1", "t-1")]["analysis_json"] = json.dumps(analysis(AI_NONE))
        out = run(rec.confirm_qualifying_context(s.session(), user(), "job-1", AI_NONE))
        assert out["qualifying_context"]["source"] == "recruiter"


# ── P2 worker can never overwrite a P3 recruiter result ─────────────────────

def ai_run(state="identified", contexts=("kilns and mills",), status="ok"):
    qc = QualifyingContext(state, tuple(contexts), "analysis") if status == "ok" else None
    return QCRunResult(status=status, qualifying_context=qc, error=None if status == "ok" else "boom",
                       prompt_version=runner.PROMPT_VERSION, prompt_sha256=runner.PROMPT_SHA256,
                       model="gpt-4o-mini", temperature=0.2, max_tokens=200, jd_sha256=JD_SHA)


class TestWorkerCannotOverwriteRecruiter:
    @pytest.mark.parametrize("action", ["confirm", "edit"])
    @pytest.mark.parametrize("run_result", [None, ai_run(), ai_run(status="failed_technical")],
                             ids=["flag_off", "ai_success", "ai_failure"])
    def test_merge_keeps_recruiter(self, action, run_result):
        base = analysis(AI_IDENTIFIED)
        t = (rec.build_confirm(base, AI_IDENTIFIED, user(), NOW) if action == "confirm"
             else rec.build_edit(base, "identified", ["my wording"], AI_IDENTIFIED, user(), NOW))
        fresh = analysis(None, audit=False)
        out = pers.merge_qualifying_context(t.analysis, fresh, run_result, generated_at=NOW,
                                            current_jd_sha256=JD_SHA)
        assert out.analysis["experience"]["qualifying_context"] == t.new_qc
        assert out.analysis["qualifying_context_audit"]["current"] == \
            t.analysis["qualifying_context_audit"]["current"]
        assert "recruiter" not in json.dumps(out.original)
        if run_result is not None:
            lr = out.analysis["qualifying_context_audit"]["latest_run"]
            assert lr["not_applied_reason"] == "recruiter_owned"
            assert rec.review_status(out.analysis)["status"] in ("confirmed", "edited")


# ── router wiring (static: routers are not imported in tests) ───────────────

JOBS_SRC = (BACKEND / "routers" / "jobs.py").read_text(encoding="utf-8")
JOBS_AST = ast.parse(JOBS_SRC)


def _fn(name):
    return next(n for n in ast.walk(JOBS_AST) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)


class TestRouterWiring:
    def test_criteria_content_read_is_locked(self):
        src = ast.get_source_segment(JOBS_SRC, _fn("update_criteria_content"))
        assert re.search(r"SELECT analysis_json FROM job_criteria WHERE job_id = :jid FOR UPDATE", src)
        fn = _fn("update_criteria_content")
        sql_literals = [c.args[0].value for c in ast.walk(fn) if isinstance(c, ast.Call)
                        and getattr(c.func, "id", None) == "text" and c.args
                        and isinstance(c.args[0], ast.Constant) and isinstance(c.args[0].value, str)]
        locked = [q for q in sql_literals if "FOR UPDATE" in q]
        assert locked == ["SELECT analysis_json FROM job_criteria WHERE job_id = :jid FOR UPDATE"]
        assert "original_analysis_json" not in src.split('"""', 2)[2]     # still never written

    def test_endpoints_and_contracts(self):
        edit = ast.get_source_segment(JOBS_SRC, _fn("edit_qualifying_context"))
        conf = ast.get_source_segment(JOBS_SRC, _fn("confirm_qualifying_context"))
        assert '@router.put("/{job_id}/criteria/qualifying-context")' in JOBS_SRC
        assert '@router.post("/{job_id}/criteria/qualifying-context/confirm")' in JOBS_SRC
        assert "qc_recruiter.edit_qualifying_context(" in edit and "current_user" in edit
        assert "qc_recruiter.confirm_qualifying_context(" in conf and "current_user" in conf
        for src in (edit, conf):
            assert "detail=exc.detail()" in src and "status_code=exc.http_status" in src
        models = {n.name: n for n in ast.walk(JOBS_AST) if isinstance(n, ast.ClassDef)}
        fields = {m: [t.target.id for t in models[m].body if isinstance(t, ast.AnnAssign)]
                  for m in ("EditQualifyingContextRequest", "ConfirmQualifyingContextRequest")}
        assert fields == {"EditQualifyingContextRequest": ["state", "contexts", "expected_qualifying_context"],
                          "ConfirmQualifyingContextRequest": ["expected_qualifying_context"]}
        for m in fields:     # expected_qualifying_context has no default -> required (may be null)
            ann = next(t for t in models[m].body if isinstance(t, ast.AnnAssign)
                       and t.target.id == "expected_qualifying_context")
            assert ann.value is None

    def test_details_returns_review(self):
        assert '"qualifying_context_review": _qc_review_status(analysis_json)' in JOBS_SRC


# ── isolation and invariance ────────────────────────────────────────────────

class TestIsolation:
    def test_recruiter_module_never_imports_model_code(self):
        tree = ast.parse((BACKEND / "services" / "qualifying_context" / "recruiter.py").read_text())
        mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not {m for m in mods if any(k in m for k in ("openai", "runner", "ai_service", "llm"))}

    def test_scoring_unchanged_with_recruiter_object(self):
        from services.ai_service import flatten_criteria_for_scoring
        from services.llm_criteria_mapper import _flatten_criteria
        plain = analysis(None, audit=False)
        t = rec.build_edit(analysis(AI_IDENTIFIED), "identified", ["kilns"], AI_IDENTIFIED, user(), NOW)
        assert flatten_criteria_for_scoring(t.analysis) == flatten_criteria_for_scoring(plain)
        assert _flatten_criteria(t.analysis) == _flatten_criteria(plain)

    @pytest.mark.parametrize("rel,sha", [
        ("scripts/qc_eval_fixtures/prompts/candidate_qc-1.txt",
         "fc979dd4a48b2aa5922c87f62e613700f898da13599e43e62a4fbc1304c851df"),
        ("services/qualifying_context/prompts/qc-1.txt",
         "fc979dd4a48b2aa5922c87f62e613700f898da13599e43e62a4fbc1304c851df"),
        ("scripts/qc_eval_fixtures/qc_main_cases.json",
         "c5fcd5024d9673217d0b1e8b8413d4628025932b86fcb97f56b96fc57f98317c"),
        ("scripts/qc_eval_fixtures/qc_heldout_cases.json",
         "d94a39f343e0185cf9d0cca664d1c744f52d5b66b6b32ac57edee22ed3a1b91f")])
    def test_frozen_artifacts(self, rel, sha):
        assert hashlib.sha256((BACKEND / rel).read_bytes()).hexdigest() == sha
