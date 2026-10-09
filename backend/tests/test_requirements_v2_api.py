"""Requirements-v2 editing and review API (services/requirements_api.py + routers/job_requirements.py).

Synthetic data only. The database is a scripted in-memory stand-in that models what the API relies on from PostgreSQL:
row lock on SELECT ... FOR UPDATE (held until commit / rollback), writes staged until commit, a revision-guarded
UPDATE, an audit table written through the same session. Nothing touches a real database, a model or the network.
"""
from __future__ import annotations

import asyncio
import copy
import importlib
import json
import pathlib
import re
import sys
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import test_p001_scoring_path as p001
from test_requirements_v2_classification_ack import extract, flagged_job, ids_by_text, raw_item
from services.requirements_guard import REQUIREMENTS_V2_SCORING_SUPPORTED, UnsupportedEvaluationError, assert_job_evaluable
from services.requirements_v2 import (
    CATEGORIES, POLICY_KEY, basis_hash, compute_readiness, confirm_no_numeric_score, validate_final,
)

BACKEND = pathlib.Path(__file__).resolve().parent.parent
JOB = "00000000-0000-0000-0000-0000000000aa"
OTHER_JOB = "00000000-0000-0000-0000-0000000000bb"
T1, T2 = "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"
NOW = "2026-02-02T12:00:00+00:00"


def user(role="hr_manager", tenant=T1, uid="00000000-0000-0000-0000-0000000000u1"):
    return SimpleNamespace(user_id=uid, tenant_id=tenant, email=f"{role}@example.com", role=role, full_name=role)


@pytest.fixture(autouse=True)
def _real_modules():
    with patch.dict(sys.modules, p001._REAL_MODULES):
        yield


@pytest.fixture
def api(monkeypatch):
    mod = importlib.import_module("services.requirements_api")
    monkeypatch.setattr(mod, "_now", lambda: NOW)
    return mod


# ══ the in-memory database ════════════════════════════════════════════════════════════════════════════════════════

class Result:
    def __init__(self, first=None, scalar=None, rowcount=0):
        self._first, self._scalar, self.rowcount = first, scalar, rowcount

    def mappings(self):
        return self

    def first(self):
        return self._first

    def scalar_one_or_none(self):
        return self._scalar


class Store:
    def __init__(self, policy="true", use_lock=True):
        self.jobs: dict[str, dict] = {}
        self.rows: dict[str, dict] = {}
        self.agency: set[tuple[str, str]] = set()
        self.config: dict[str, str] = {POLICY_KEY: policy} if policy is not None else {}
        self.audit: list[dict] = []
        self.lock = asyncio.Lock()
        self.use_lock = use_lock
        self.fail_audit = False
        self.json_as_text = False

    def add_job(self, doc, original, *, job_id=JOB, tenant=T1, client_org=None, marker=2, revision=0, extra=None,
                legacy=False):
        self.jobs[job_id] = {"tenant": tenant, "client_org": client_org}
        analysis = dict(extra or {})
        if not legacy:
            analysis["requirements"] = copy.deepcopy(doc)
        self.rows[job_id] = {
            "analysis": analysis,
            "original_analysis": {"requirements": copy.deepcopy(original)} if original is not None and not legacy else {"skills": {}},
            "marker": None if legacy else marker, "revision": revision, "retired": [],
            "weights": {c: (doc["categories"][c]["weight"] if doc else 0) for c in CATEGORIES} if not legacy else None,
        }
        return job_id

    def session(self):
        return Session(self)

    def actions(self):
        return [a["action"] for a in self.audit]

    def audit_of(self, action):
        return [a for a in self.audit if a["action"] == action]


class Session:
    def __init__(self, store: Store):
        self.store, self.sql, self.staged, self.staged_audit, self.holds = store, [], None, [], False
        self.commits = self.rollbacks = 0

    def _release(self):
        if self.holds:
            self.store.lock.release()
            self.holds = False

    async def execute(self, stmt, params=None):
        await asyncio.sleep(0)                      # a real driver yields here: lets a concurrent request interleave
        sql, params, s = str(stmt), params or {}, self.store
        self.sql.append(sql)
        if "FROM jobs j" in sql:
            job = s.jobs.get(params["jid"])
            if not job or job["tenant"] != params["tid"]:
                return Result()
            if not params["is_admin"] and job["client_org"] is not None \
                    and (params["uid"], job["client_org"]) not in s.agency:
                return Result()
            return Result(first=(params["jid"],))
        if "FROM job_criteria jc" in sql:
            if "FOR UPDATE" in sql and s.use_lock:
                await s.lock.acquire()
                self.holds = True
            row = s.rows.get(params["jid"])
            if row is None:
                return Result()
            enc = (lambda v: json.dumps(v)) if s.json_as_text else (lambda v: copy.deepcopy(v))
            return Result(first={
                "analysis_json": enc(row["analysis"]), "original_analysis_json": enc(row["original_analysis"]),
                "requirements_schema_version": None if row["marker"] is None else str(row["marker"]),
                "requirements_revision": None if row["revision"] is None else str(row["revision"]),
                "requirements_retired_item_ids": enc(row["retired"]) if row["revision"] is not None else None})
        if "UPDATE job_criteria" in sql:
            if not self.holds:                      # an UPDATE takes the row lock itself and re-checks its WHERE
                await s.lock.acquire()              # after the competing transaction committed (READ COMMITTED)
                self.holds = True
            row = s.rows[params["jid"]]
            if row["revision"] != params["rev"]:
                return Result(rowcount=0)
            self.staged = (params["jid"], dict(
                row, analysis=json.loads(params["aj"]), retired=json.loads(params["retired"]),
                revision=row["revision"] + 1, weights={c: params[f"w_{c}"] for c in CATEGORIES},
                last_edited_by=params["uid"]))
            return Result(rowcount=1)
        if "FROM system_config" in sql:
            return Result(scalar=s.config.get(params["k"]))
        if "INSERT INTO audit_logs" in sql:
            if s.fail_audit:
                raise RuntimeError("audit table unavailable")
            self.staged_audit.append({"action": params["action"], "user_id": params["user_id"],
                                      "tenant_id": params["tenant_id"], "resource_id": params["resource_id"],
                                      "details": json.loads(params["details"]) if params["details"] else None})
            return Result()
        return Result()

    async def commit(self):
        self.commits += 1
        if self.staged:
            job_id, row = self.staged
            self.store.rows[job_id] = row
        self.store.audit.extend(self.staged_audit)
        self.staged, self.staged_audit = None, []
        self._release()

    async def rollback(self):
        self.rollbacks += 1
        self.staged, self.staged_audit = None, []
        self._release()

    def wrote(self):
        return any("UPDATE job_criteria" in s for s in self.sql)


# ══ synthetic jobs and client-side helpers ═══════════════════════════════════════════════════════════════════════

def seed(store, result=None, **kw):
    result = result or flagged_job()
    store.add_job(result.requirements, result.original, **kw)
    return result


def preferred_only_job():
    r = extract("Requirements:\n- SQL is a plus\n- Docker is a plus\n",
                raw_item("SQL", "SQL is a plus", "preferred", "is a plus"),
                raw_item("Docker", "Docker is a plus", "preferred", "is a plus"))
    assert r.requirements and compute_readiness(r.requirements).state == "needs_confirmation"
    return r


def client_doc(view):
    """What a UI would send back: the document it was shown (public part only)."""
    return copy.deepcopy(view["requirements"])


def item(doc, text):
    for c in CATEGORIES:
        for i in doc["categories"][c]["items"]:
            if i["text"] == text:
                return c, i
    raise KeyError(text)


def to_required(doc, text, category="skills"):
    """Client-side: make `text` Required and give the category's required items equal weights (sum 100)."""
    _, i = item(doc, text)
    i["importance"], i["weight"] = "required", None
    req = [x for x in doc["categories"][category]["items"] if x["importance"] == "required"]
    for n, x in enumerate(req):
        x["weight"] = 100 // len(req) + (1 if n < 100 % len(req) else 0)
    return doc


def to_preferred(doc, text, category="skills"):
    _, i = item(doc, text)
    i["importance"], i["weight"] = "preferred", None
    req = [x for x in doc["categories"][category]["items"] if x["importance"] == "required"]
    for n, x in enumerate(req):
        x["weight"] = 100 // len(req) + (1 if n < 100 % len(req) else 0)
    if not req:
        doc["categories"][category]["weight"] = 0
    return doc


async def save(api, store, doc, rev, who=None, job=JOB):
    return await api.save_requirements(store.session(), who or user(), job, rev, doc)


async def ack(api, store, warning, rev, who=None, job=JOB):
    return await api.acknowledge_warning(store.session(), who or user(), job, rev, warning)


async def view(api, store, who=None, job=JOB):
    return await api.get_requirements(store.session(), who or user(), job)


async def raises(coro, status, code=None):
    with pytest.raises(Exception) as ei:
        await coro
    exc = ei.value
    assert getattr(exc, "http_status", None) == status, repr(exc)
    if code:
        assert exc.code == code, exc.code
    return exc


def warning_state(v, wid):
    return next(w["state"] for w in v["classification_warnings"] if w["id"] == wid)


# ══ reading ═════════════════════════════════════════════════════════════════════════════════════════════════════

class TestReading:

    @pytest.mark.asyncio
    async def test_exposes_requirements_original_readiness_warnings_and_edited_flags(self, api):
        store = Store()
        r = seed(store)
        db = store.session()
        v = await api.get_requirements(db, user(), JOB)
        assert v["revision"] == 0 and v["job_id"] == JOB and v["can_edit"] is True
        assert v["requirements"]["categories"]["skills"]["items"][0]["text"] == "Python"
        assert v["original"] == {"schema_version": 2, "categories": r.original["categories"]}
        assert v["edited_categories"] == {c: False for c in CATEGORIES}
        assert v["readiness"]["state"] == "needs_classification_review"
        assert len(v["readiness"]["unresolved_warning_ids"]) == 2
        assert {w["code"] for w in v["classification_warnings"]} == {"preferred_cue_missing",
                                                                      "preferred_cue_not_linked_to_item"}
        assert all(w["state"] == "unresolved" and w["acknowledgment"] is None for w in v["classification_warnings"])
        assert v["classification_policy"] == {"key": POLICY_KEY, "require_acknowledgment": True}
        assert v["preferred_only_confirmation"] == {"confirmed": False}
        # server-owned internals are not part of the editable document that is sent back
        assert set(v["requirements"]) == {"schema_version", "categories"}

    @pytest.mark.asyncio
    async def test_a_read_takes_no_lock_writes_nothing_and_logs_nothing(self, api):
        store = Store()
        seed(store)
        db = store.session()
        await api.get_requirements(db, user(role="viewer"), JOB)
        assert not any("FOR UPDATE" in s for s in db.sql) and not db.wrote()
        assert db.commits == 0 and store.audit == []

    @pytest.mark.asyncio
    async def test_any_role_with_job_access_may_read_but_is_told_it_cannot_edit(self, api):
        store = Store()
        seed(store)
        v = await view(api, store, user(role="recruiter"))
        assert v["can_edit"] is False

    @pytest.mark.asyncio
    async def test_jsonb_returned_as_text_is_understood(self, api):
        store = Store()
        store.json_as_text = True
        seed(store)
        assert (await view(api, store))["revision"] == 0

    @pytest.mark.asyncio
    async def test_edited_flags_follow_the_original_and_clear_when_restored(self, api):
        store = Store()
        seed(store)
        v = await view(api, store)
        doc = client_doc(v)
        item(doc, "SQL")[1]["text"] = "SQL (advanced)"
        v1 = await save(api, store, doc, 0)
        assert [c for c, f in v1["edited_categories"].items() if f] == ["skills"]
        doc = client_doc(v1)
        item(doc, "SQL (advanced)")[1]["text"] = "SQL"
        v2 = await save(api, store, doc, 1)
        assert v2["edited_categories"] == {c: False for c in CATEGORIES}


# ══ authorization, tenant isolation, legacy jobs ═════════════════════════════════════════════════════════════════

class TestAuthorization:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("role", ["recruiter", "viewer", "super_admin", "", None])
    async def test_only_admin_and_hr_manager_may_write(self, api, role):
        store = Store()
        r = seed(store)
        wid = f"{ids_by_text(r.requirements)['Docker']}:preferred_cue_missing"
        who = user(role=role)
        doc = client_doc(await view(api, store))
        for coro in (save(api, store, doc, 0, who), ack(api, store, wid, 0, who), api.confirm_no_score(store.session(), who, JOB, 0)):
            await raises(coro, 403, "forbidden")
        assert store.rows[JOB]["revision"] == 0 and store.audit == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("role", ["admin", "hr_manager"])
    async def test_admin_and_hr_manager_may_write(self, api, role):
        store = Store()
        seed(store)
        doc = client_doc(await view(api, store))
        item(doc, "SQL")[1]["text"] = "SQL 2"
        assert (await save(api, store, doc, 0, user(role=role)))["revision"] == 1

    @pytest.mark.asyncio
    async def test_another_tenants_job_is_not_found_for_reads_and_writes(self, api):
        store = Store()
        r = seed(store)
        stranger = user(role="admin", tenant=T2)
        wid = f"{ids_by_text(r.requirements)['Docker']}:preferred_cue_missing"
        doc = client_doc(await view(api, store))
        await raises(api.get_requirements(store.session(), stranger, JOB), 404, "not_found")
        await raises(save(api, store, doc, 0, stranger), 404, "not_found")
        await raises(ack(api, store, wid, 0, stranger), 404, "not_found")
        await raises(api.confirm_no_score(store.session(), stranger, JOB, 0), 404, "not_found")
        assert store.rows[JOB]["revision"] == 0 and store.audit == []

    @pytest.mark.asyncio
    async def test_a_malformed_job_id_is_not_found(self, api):
        store = Store()
        seed(store)
        await raises(api.get_requirements(store.session(), user(), "not-a-uuid"), 404, "not_found")

    @pytest.mark.asyncio
    async def test_an_unknown_job_is_not_found(self, api):
        store = Store()
        seed(store)
        await raises(api.get_requirements(store.session(), user(), OTHER_JOB), 404, "not_found")

    @pytest.mark.asyncio
    async def test_agency_hr_manager_needs_the_client_assignment_admin_does_not(self, api):
        store = Store()
        seed(store, client_org="org-1")
        who = user(role="hr_manager")
        await raises(api.get_requirements(store.session(), who, JOB), 404, "not_found")
        assert (await view(api, store, user(role="admin")))["revision"] == 0
        store.agency.add((who.user_id, "org-1"))
        assert (await view(api, store, who))["revision"] == 0

    @pytest.mark.asyncio
    async def test_a_legacy_job_is_refused_and_never_touched(self, api):
        store = Store()
        seed(store, legacy=True)
        before = copy.deepcopy(store.rows[JOB])
        db = store.session()
        await raises(api.get_requirements(db, user(), JOB), 409, "not_requirements_v2")
        await raises(api.save_requirements(store.session(), user(), JOB, 0, {"schema_version": 2}), 409,
                     "not_requirements_v2")
        await raises(api.acknowledge_warning(store.session(), user(), JOB, 0, "x:y"), 409, "not_requirements_v2")
        await raises(api.confirm_no_score(store.session(), user(), JOB, 0), 409, "not_requirements_v2")
        assert store.rows[JOB] == before and store.audit == []

    @pytest.mark.asyncio
    async def test_a_job_with_the_marker_but_no_document_yet_is_refused(self, api):
        store = Store()
        seed(store)
        del store.rows[JOB]["analysis"]["requirements"]
        await raises(api.get_requirements(store.session(), user(), JOB), 409, "requirements_missing")

    @pytest.mark.asyncio
    async def test_a_v2_block_without_the_marker_is_still_v2(self, api):
        store = Store()
        seed(store, marker=None)                     # analysis shape only, like the guard's second signal
        assert (await view(api, store))["revision"] == 0

    @pytest.mark.asyncio
    async def test_a_malformed_stored_document_is_refused_not_repaired(self, api):
        store = Store()
        seed(store)
        store.rows[JOB]["analysis"]["requirements"]["categories"].pop("skills")
        await raises(save(api, store, {"schema_version": 2, "categories": {}}, 0), 409, "stored_requirements_invalid")

    def test_routes_use_the_module_guard_and_the_expected_shapes(self):
        router = importlib.import_module("routers.job_requirements")
        paths = {(tuple(sorted(r.methods)), r.path) for r in router.router.routes}
        assert paths == {
            (("GET",), "/jobs/{job_id}/requirements"), (("PUT",), "/jobs/{job_id}/requirements"),
            (("POST",), "/jobs/{job_id}/requirements/classification-warnings/acknowledge"),
            (("POST",), "/jobs/{job_id}/requirements/confirm-no-numeric-score"),
            (("POST",), "/jobs/{job_id}/requirements/structure-review/confirm")}
        assert router.router.dependencies, "the AI-recruitment module guard must protect these routes"

    def test_request_bodies_reject_extra_fields_and_non_integer_revisions(self):
        router = importlib.import_module("routers.job_requirements")
        from pydantic import ValidationError
        for bad in ({"expected_revision": "1", "requirements": {}}, {"expected_revision": True, "requirements": {}},
                    {"expected_revision": -1, "requirements": {}}, {"requirements": {}},
                    {"expected_revision": 0, "requirements": {}, "revision": 5}):
            with pytest.raises(ValidationError):
                router.SaveRequirementsRequest(**bad)
        with pytest.raises(ValidationError):          # the user and the time are never request fields
            router.AcknowledgeWarningRequest(expected_revision=0, warning_id="a:b", user_id="u", acknowledged_at="t")
        with pytest.raises(ValidationError):
            router.ConfirmNoScoreRequest(expected_revision=0, confirmed_by="u")

    @pytest.mark.asyncio
    async def test_the_router_maps_service_errors_to_http_errors(self, api):
        router = importlib.import_module("routers.job_requirements")
        store = Store()
        seed(store)
        body = router.SaveRequirementsRequest(expected_revision=0, requirements={"schema_version": 2})
        with pytest.raises(HTTPException) as ei:
            await router.save_requirements(JOB, body, user(role="recruiter"), store.session())
        assert ei.value.status_code == 403 and ei.value.detail["code"] == "forbidden"
        ok = await router.get_requirements(JOB, user(), store.session())
        assert ok["revision"] == 0


# ══ revisions and concurrency ═══════════════════════════════════════════════════════════════════════════════════

class TestConcurrency:

    @pytest.mark.asyncio
    async def test_a_stale_revision_is_refused_with_the_current_state_and_stores_nothing(self, api):
        store = Store()
        seed(store)
        doc = client_doc(await view(api, store))
        item(doc, "SQL")[1]["text"] = "SQL 2"
        await save(api, store, doc, 0)                                           # someone else saved first
        stale = client_doc(await view(api, store))
        item(stale, "SQL 2")[1]["text"] = "mine"
        exc = await raises(save(api, store, stale, 0), 409, "requirements_revision_conflict")
        assert exc.extra["current"]["revision"] == 1
        assert item(store.rows[JOB]["analysis"]["requirements"], "SQL 2")                # the first edit survived
        assert store.rows[JOB]["revision"] == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("use_lock", [True, False], ids=["row-lock", "update-guard-only"])
    async def test_two_concurrent_saves_at_one_revision_cannot_both_win(self, api, use_lock):
        store = Store(use_lock=use_lock)
        seed(store)
        base = client_doc(await view(api, store))
        a, b = copy.deepcopy(base), copy.deepcopy(base)
        item(a, "SQL")[1]["text"] = "from A"
        item(b, "Docker")[1]["text"] = "from B"
        results = await asyncio.gather(save(api, store, a, 0), save(api, store, b, 0), return_exceptions=True)
        wins = [r for r in results if isinstance(r, dict)]
        losses = [r for r in results if isinstance(r, api.ApiError)]
        assert len(wins) == 1 and len(losses) == 1 and losses[0].code == "requirements_revision_conflict"
        assert store.rows[JOB]["revision"] == 1
        texts = [i["text"] for i in store.rows[JOB]["analysis"]["requirements"]["categories"]["skills"]["items"]]
        assert ("from A" in texts) != ("from B" in texts)                           # exactly one edit, not a blend
        assert len(store.audit_of("requirements_saved")) == 1
        assert not store.lock.locked()

    @pytest.mark.asyncio
    async def test_a_save_and_an_acknowledgment_cannot_overwrite_each_other(self, api):
        store = Store()
        r = seed(store)
        wid = f"{ids_by_text(r.requirements)['Docker']}:preferred_cue_missing"
        doc = client_doc(await view(api, store))
        item(doc, "SQL")[1]["text"] = "SQL 2"
        results = await asyncio.gather(save(api, store, doc, 0), ack(api, store, wid, 0), return_exceptions=True)
        assert sum(isinstance(r, dict) for r in results) == 1
        assert sum(isinstance(r, api.ApiError) and r.code == "requirements_revision_conflict" for r in results) == 1
        assert store.rows[JOB]["revision"] == 1

    @pytest.mark.asyncio
    async def test_the_revision_advances_once_per_write_and_not_on_a_no_op(self, api):
        store = Store()
        r = seed(store)
        v = await view(api, store)
        same = await save(api, store, client_doc(v), 0)                            # nothing changed
        assert same["changed"] is False and same["revision"] == 0 and store.audit == []
        doc = client_doc(v)
        item(doc, "SQL")[1]["text"] = "SQL 2"
        assert (await save(api, store, doc, 0))["revision"] == 1
        wid = f"{ids_by_text(r.requirements)['Docker']}:preferred_cue_missing"
        assert (await ack(api, store, wid, 1))["revision"] == 2

    @pytest.mark.asyncio
    async def test_the_writes_are_serialised_by_a_row_lock_held_until_commit(self, api):
        store = Store()
        seed(store)
        doc = client_doc(await view(api, store))
        item(doc, "SQL")[1]["text"] = "SQL 2"
        db = store.session()
        await api.save_requirements(db, user(), JOB, 0, doc)
        assert any("FOR UPDATE" in s for s in db.sql)
        assert db.commits == 1 and not store.lock.locked()

    @pytest.mark.asyncio
    async def test_editing_is_refused_until_the_revision_migration_is_applied(self, api):
        store = Store()
        seed(store, revision=None)
        doc = client_doc(await view(api, store))
        await raises(save(api, store, doc, 0), 503, "requirements_migration_missing")
        assert store.audit == [] and not store.lock.locked()


# ══ saving: weights, originals, identity, forged metadata ════════════════════════════════════════════════════════

class TestSaving:

    @pytest.mark.asyncio
    async def test_json_and_the_seven_weight_columns_are_written_together_in_one_statement(self, api):
        store = Store()
        seed(store, extra={"qualifying_context_audit": {"keep": "me"}})
        v = await view(api, store)
        doc = client_doc(v)
        to_required(doc, "Docker")                                                   # skills: Python 50 / Docker 50
        doc["categories"]["education"]["items"].append({"text": "BSc", "importance": "required", "weight": 100})
        doc["categories"]["skills"]["weight"], doc["categories"]["education"]["weight"] = 70, 30
        db = store.session()
        out = await api.save_requirements(db, user(), JOB, 0, doc)
        row = store.rows[JOB]
        assert sum(1 for s in db.sql if "UPDATE job_criteria" in s) == 1 and db.commits == 1
        assert row["weights"] == {c: row["analysis"]["requirements"]["categories"][c]["weight"] for c in CATEGORIES}
        assert row["weights"]["skills"] == 70 and row["weights"]["education"] == 30 and sum(row["weights"].values()) == 100
        assert out["requirements"]["categories"]["education"]["items"][0]["weight"] == 100
        assert row["analysis"]["qualifying_context_audit"] == {"keep": "me"}           # other analysis keys untouched
        assert row["last_edited_by"] == user().user_id

    @pytest.mark.asyncio
    async def test_the_original_snapshot_is_never_written(self, api):
        store = Store()
        seed(store)
        before = copy.deepcopy(store.rows[JOB]["original_analysis"])
        doc = client_doc(await view(api, store))
        to_required(doc, "Docker")
        db = store.session()
        out = await api.save_requirements(db, user(), JOB, 0, doc)
        assert store.rows[JOB]["original_analysis"] == before
        assert "original_analysis_json" not in " ".join(s for s in db.sql if s.lstrip().startswith("UPDATE"))
        assert out["original"]["categories"] == before["requirements"]["categories"]
        assert out["edited_categories"]["skills"] is True

    @pytest.mark.asyncio
    async def test_a_save_without_an_original_snapshot_is_refused(self, api):
        store = Store()
        seed(store)
        store.rows[JOB]["original_analysis"] = {}
        await raises(save(api, store, client_doc(await view(api, store)), 0), 409, "original_snapshot_missing")

    @pytest.mark.asyncio
    async def test_unbalanced_weights_are_refused_with_the_issues_and_nothing_is_stored(self, api):
        store = Store()
        seed(store)
        doc = client_doc(await view(api, store))
        _, docker = item(doc, "Docker")
        docker["importance"], docker["weight"] = "required", 30                       # Python 100 + Docker 30
        exc = await raises(save(api, store, doc, 0), 422, "invalid_requirements")
        assert "required_weights_total" in [i["code"] for i in exc.extra["issues"]]
        assert store.rows[JOB]["revision"] == 0 and store.audit == []

    @pytest.mark.asyncio
    async def test_the_server_never_redistributes_weights(self, api):
        store = Store()
        seed(store)
        doc = client_doc(await view(api, store))
        doc["categories"]["skills"]["items"].append({"text": "Go", "importance": "required", "weight": None})
        await raises(save(api, store, doc, 0), 422, "invalid_requirements")           # not auto-balanced
        # a hand-adjusted, valid split is stored exactly as sent
        doc = client_doc(await view(api, store))
        doc["categories"]["skills"]["items"].append({"text": "Go", "importance": "required", "weight": 15})
        item(doc, "Python")[1]["weight"] = 85
        out = await save(api, store, doc, 0)
        assert [i["weight"] for i in out["requirements"]["categories"]["skills"]["items"] if i["importance"] == "required"] == [85, 15]

    @pytest.mark.asyncio
    async def test_a_preferred_item_cannot_carry_a_weight_and_acknowledging_never_gives_one(self, api):
        store = Store()
        r = seed(store)
        doc = client_doc(await view(api, store))
        item(doc, "SQL")[1]["weight"] = 10
        exc = await raises(save(api, store, doc, 0), 422, "invalid_requirements")
        assert "preferred_item_has_weight" in [i["code"] for i in exc.extra["issues"]]
        wid = f"{ids_by_text(r.requirements)['Docker']}:preferred_cue_missing"
        out = await ack(api, store, wid, 0)
        assert all(i["weight"] is None for i in out["requirements"]["categories"]["skills"]["items"]
                   if i["importance"] == "preferred")

    @pytest.mark.asyncio
    async def test_duplicates_within_and_across_categories_are_allowed(self, api):
        store = Store()
        seed(store)
        doc = client_doc(await view(api, store))
        doc["categories"]["skills"]["items"].append({"text": "SQL", "importance": "preferred", "weight": None})
        doc["categories"]["domain_knowledge"]["items"].append({"text": "SQL", "importance": "preferred", "weight": None})
        out = await save(api, store, doc, 0)
        assert out["revision"] == 1

    @pytest.mark.asyncio
    async def test_existing_items_keep_their_identity_and_provenance(self, api):
        store = Store()
        r = seed(store)
        ids = ids_by_text(r.requirements)
        doc = client_doc(await view(api, store))
        to_required(doc, "Docker")
        out = await save(api, store, doc, 0)
        assert ids_by_text(out["requirements"]) == ids
        stored = store.rows[JOB]["analysis"]["requirements"]["categories"]["skills"]["items"]
        orig = {i["id"]: i for i in r.requirements["categories"]["skills"]["items"]}
        for i in stored:
            assert (i["origin"], i["source_text"]) == (orig[i["id"]]["origin"], orig[i["id"]]["source_text"])

    @pytest.mark.asyncio
    async def test_new_items_get_a_server_id_and_recruiter_provenance(self, api):
        store = Store()
        r = seed(store)
        doc = client_doc(await view(api, store))
        doc["categories"]["certifications"]["items"].append(
            {"text": "PMP", "importance": "preferred", "weight": None, "origin": "stated", "source_text": "made up"})
        out = await save(api, store, doc, 0)
        _, pmp = item(out["requirements"], "PMP")
        assert re.fullmatch(r"req_[0-9a-f]{12}", pmp["id"])
        assert (pmp["origin"], pmp["source_text"]) == ("recruiter_added", None)
        assert "categories.certifications.items[0].origin/source_text" in out["discarded_client_fields"]

    @pytest.mark.asyncio
    async def test_invented_retired_or_original_ids_are_refused(self, api):
        store = Store()
        r = seed(store)
        base = client_doc(await view(api, store))
        forged = copy.deepcopy(base)
        forged["categories"]["skills"]["items"].append({"id": "req_000000000000", "text": "X", "importance": "preferred", "weight": None})
        await raises(save(api, store, forged, 0), 422, "invalid_requirements")
        # remove SQL, then try to bring its id back
        sql_id = ids_by_text(r.requirements)["SQL"]
        gone = copy.deepcopy(base)
        gone["categories"]["skills"]["items"] = [i for i in gone["categories"]["skills"]["items"] if i["id"] != sql_id]
        await save(api, store, gone, 0)
        assert sql_id in store.rows[JOB]["retired"]
        back = copy.deepcopy(base)
        exc = await raises(save(api, store, back, 1), 422, "invalid_requirements")
        assert exc.extra["issues"][0]["code"] == "unknown_item_id"

    @pytest.mark.asyncio
    async def test_a_new_item_can_never_reuse_a_removed_or_original_id(self, api):
        store = Store()
        r = seed(store)
        sql_id = ids_by_text(r.requirements)["SQL"]
        doc = client_doc(await view(api, store))
        doc["categories"]["skills"]["items"] = [i for i in doc["categories"]["skills"]["items"] if i["id"] != sql_id]
        await save(api, store, doc, 0)
        # force the id generator to offer the removed id first
        offered = [uuid.UUID(int=int(sql_id[4:], 16)), uuid.UUID(int=int(sql_id[4:], 16))]
        fresh = client_doc(await view(api, store))
        fresh["categories"]["skills"]["items"].append({"text": "SQL again", "importance": "preferred", "weight": None})
        real = uuid.uuid4
        calls = iter(offered)
        with patch("services.requirements_v2.contract.uuid.uuid4", lambda: next(calls, None) or real()):
            out = await save(api, store, fresh, 1)
        _, again = item(out["requirements"], "SQL again")
        assert again["id"] != sql_id

    @pytest.mark.asyncio
    async def test_a_recruiter_added_item_that_is_later_deleted_stays_retired(self, api):
        store = Store()
        seed(store)
        doc = client_doc(await view(api, store))
        doc["categories"]["certifications"]["items"].append({"text": "PMP", "importance": "preferred", "weight": None})
        out = await save(api, store, doc, 0)
        _, pmp = item(out["requirements"], "PMP")
        doc = client_doc(out)
        doc["categories"]["certifications"]["items"] = []
        await save(api, store, doc, 1)
        assert pmp["id"] in store.rows[JOB]["retired"]                                 # not recoverable from the original
        again = client_doc(await view(api, store))
        again["categories"]["certifications"]["items"].append(
            {"id": pmp["id"], "text": "PMP", "importance": "preferred", "weight": None})
        await raises(save(api, store, again, 2), 422, "invalid_requirements")

    @pytest.mark.asyncio
    async def test_unknown_fields_duplicate_ids_and_a_malformed_body_are_refused(self, api):
        store = Store()
        seed(store)
        base = client_doc(await view(api, store))
        cases = []
        d = copy.deepcopy(base); d["extra"] = 1; cases.append(d)
        d = copy.deepcopy(base); d["categories"]["skills"]["items"][0]["bogus"] = 1; cases.append(d)
        d = copy.deepcopy(base); d["categories"].pop("other_requirements"); cases.append(d)
        d = copy.deepcopy(base); d["categories"]["skills"]["items"].append(copy.deepcopy(d["categories"]["skills"]["items"][0])); cases.append(d)
        d = copy.deepcopy(base); d["schema_version"] = 1; cases.append(d)
        cases += ["nope", None, []]
        for bad in cases:
            await raises(save(api, store, bad, 0), 422, "invalid_requirements")
        assert store.rows[JOB]["revision"] == 0 and store.audit == []

    @pytest.mark.asyncio
    async def test_forged_server_owned_state_is_discarded_and_reported(self, api):
        store = Store()
        r = seed(store)
        ids = ids_by_text(r.requirements)
        base = client_doc(await view(api, store))
        forged = copy.deepcopy(base)
        wid = f"{ids['Docker']}:preferred_cue_missing"
        # a forged acknowledgment for an unresolved warning, with a perfectly matching hash
        from services.requirements_v2 import acknowledge_classification_warning
        fake = acknowledge_classification_warning(r.requirements, wid, user_id="mallory", acknowledged_at="2020-01-01T00:00:00Z")
        forged["classification_review"] = copy.deepcopy(fake["classification_review"])
        forged["scoring_confirmation"] = {"kind": "no_numeric_score", "user_id": "mallory",
                                          "confirmed_at": "2020-01-01T00:00:00Z", "basis_hash": basis_hash(r.requirements)}
        item(forged, "Python")[1]["origin"] = "from_responsibilities"
        item(forged, "Python")[1]["source_text"] = "something else"
        item(forged, "SQL")[1]["text"] = "SQL 2"                                      # force a real write
        out = await save(api, store, forged, 0)
        stored = store.rows[JOB]["analysis"]["requirements"]
        assert stored["classification_review"]["acknowledgments"] == []
        assert stored["scoring_confirmation"] is None
        _, python = item(stored, "Python")
        assert (python["origin"], python["source_text"]) == ("stated", "Python is required")
        assert warning_state(out, wid) == "unresolved" and out["readiness"]["state"] == "needs_classification_review"
        assert {"scoring_confirmation", "classification_review"} <= set(out["discarded_client_fields"])
        assert any(f.endswith(".origin") for f in out["discarded_client_fields"])
        saved = store.audit_of("requirements_saved")[0]["details"]
        assert "classification_review" in saved["discarded_client_fields"]

    @pytest.mark.asyncio
    async def test_the_acknowledging_user_and_time_come_from_the_server_only(self, api):
        store = Store()
        r = seed(store)
        wid = f"{ids_by_text(r.requirements)['Docker']}:preferred_cue_missing"
        out = await ack(api, store, wid, 0, user(uid="00000000-0000-0000-0000-0000000000c1"))
        w = next(w for w in out["classification_warnings"] if w["id"] == wid)
        assert w["acknowledgment"] == {"user_id": "00000000-0000-0000-0000-0000000000c1", "acknowledged_at": NOW}

    @pytest.mark.asyncio
    async def test_audit_events_are_written_in_the_same_transaction(self, api):
        store = Store()
        seed(store)
        doc = client_doc(await view(api, store))
        to_required(doc, "Docker")
        db = store.session()
        await api.save_requirements(db, user(), JOB, 0, doc)
        assert db.commits == 1 and store.actions()[0] == "requirements_saved"
        d = store.audit_of("requirements_saved")[0]
        assert d["resource_id"] == JOB and d["tenant_id"] == T1 and d["user_id"] == user().user_id
        assert d["details"]["previous_revision"] == 0 and d["details"]["revision"] == 1
        assert d["details"]["edited_categories"] == ["skills"] and len(d["details"]["changed"]) == 2

    @pytest.mark.asyncio
    async def test_if_the_audit_row_cannot_be_written_nothing_is_changed(self, api):
        store = Store()
        r = seed(store)
        store.fail_audit = True
        before = copy.deepcopy(store.rows[JOB])
        doc = client_doc(await view(api, store))
        item(doc, "SQL")[1]["text"] = "SQL 2"
        with pytest.raises(RuntimeError):
            await save(api, store, doc, 0)
        wid = f"{ids_by_text(r.requirements)['Docker']}:preferred_cue_missing"
        with pytest.raises(RuntimeError):
            await ack(api, store, wid, 0)
        assert store.rows[JOB] == before and store.audit == [] and not store.lock.locked()


# ══ structured requirements: experience subject / duration and OR alternatives ═════════════════════════════════

EXP_TEXT = "4 years as a Maintenance Planner"
ALT_TEXT = "SQL or PostgreSQL"


def raw_struct(text, category="skills", importance="required", cue=None, alternatives=None, experience=None, source=None):
    return category, {"text": text, "importance": importance, "importance_cue": cue, "source_text": source or text,
                      "origin": "stated", "alternatives": alternatives, "experience": experience}


def structured_job():
    r = extract("Requirements:\n- 4 years as a Maintenance Planner\n- SQL or PostgreSQL\n",
                raw_struct(EXP_TEXT, "experience", experience={"subject": "Maintenance Planner", "min_years": 4}),
                raw_struct(ALT_TEXT, alternatives=["SQL", "PostgreSQL"]))
    assert r.requirements, r.errors
    return r


def preferred_structured_job(cue="is a plus"):
    r = extract("Requirements:\n- Python is required\n- Docker or Podman is a plus\n",
                raw_struct("Python"),
                raw_struct("Docker or Podman", importance="preferred", cue=cue, alternatives=["Docker", "Podman"],
                           source="Docker or Podman is a plus" if cue else "Docker or Podman"))
    assert r.requirements, r.errors
    return r


def preferred_only_structured_job():
    r = extract("Requirements:\n- Docker or Podman is a plus\n",
                raw_struct("Docker or Podman", importance="preferred", cue="is a plus", alternatives=["Docker", "Podman"],
                           source="Docker or Podman is a plus"))
    assert r.requirements and compute_readiness(r.requirements, original=r.original).state == "needs_confirmation"
    return r


def stored_doc(store):
    return store.rows[JOB]["analysis"]["requirements"]


def struct_state(v, text_id):
    return next(i["state"] for i in v["structure_review"]["items"] if i["item_id"] == text_id)


def item_id(store, text):
    return item(stored_doc(store), text)[1]["id"]


class TestStructuredEditing:

    @pytest.mark.asyncio
    async def test_an_unedited_structured_job_is_settled_by_the_original_and_ready(self, api):
        store = Store()
        seed(store, structured_job())
        v = await view(api, store)
        assert [(i["state"], i["record"]) for i in v["structure_review"]["items"]] == [("original", None)] * 2
        assert v["structure_review"]["needs_review_item_ids"] == [] and v["readiness"]["state"] == "ready"

    @pytest.mark.asyncio
    async def test_a_wording_only_edit_needs_review_and_keeps_everything_else(self, api):
        store = Store()
        r = seed(store, structured_job())
        before_original = copy.deepcopy(store.rows[JOB]["original_analysis"])
        ids = ids_by_text(r.requirements)
        doc = client_doc(await view(api, store))
        item(doc, EXP_TEXT)[1]["text"] = "7 years as a Maintenance Planner"
        out = await save(api, store, doc, 0)
        stored = stored_doc(store)
        _, exp = item(stored, "7 years as a Maintenance Planner")
        assert exp["id"] == ids[EXP_TEXT] and exp["experience"] == {"subject": "Maintenance Planner", "min_years": 4}
        assert (exp["origin"], exp["source_text"]) == ("stated", EXP_TEXT)                    # source wording kept
        assert out["readiness"]["state"] == "needs_structure_review" and not out["readiness"]["can_proceed"]
        assert out["readiness"]["structure_review_item_ids"] == [exp["id"]]
        assert out["readiness"]["reasons"][0]["code"] == "structure_review_pending"
        assert struct_state(out, exp["id"]) == "needs_review"
        assert "structure_review" not in stored or stored["structure_review"]["records"] == []   # no record created
        assert store.rows[JOB]["original_analysis"] == before_original
        assert [a for a in store.actions() if "structure" in a] == []

    @pytest.mark.asyncio
    async def test_saving_the_same_structured_values_again_is_not_a_confirmation(self, api):
        store = Store()
        seed(store, structured_job())
        doc = client_doc(await view(api, store))
        item(doc, ALT_TEXT)[1]["text"] = "SQL or Postgres"
        v1 = await save(api, store, doc, 0)
        again = client_doc(v1)                                                                  # resubmit it all, unchanged
        item(again, "SQL or Postgres")[1]["alternatives"] = ["SQL", "PostgreSQL"]               # same values, explicitly
        v2 = await save(api, store, again, 1)
        assert v2["changed"] is False and v2["readiness"]["state"] == "needs_structure_review"
        again2 = client_doc(v2)
        item(again2, "SQL or Postgres")[1]["text"] = "SQL or Postgres!"                         # another wording edit
        v3 = await save(api, store, again2, 1)
        assert v3["readiness"]["state"] == "needs_structure_review"
        assert not store.audit_of("requirements_structure_recorded") and not store.audit_of("requirements_structure_confirmed")

    @pytest.mark.asyncio
    async def test_explicit_confirmation_is_recorded_with_user_time_and_what_was_confirmed(self, api):
        store = Store()
        seed(store, structured_job())
        doc = client_doc(await view(api, store))
        item(doc, ALT_TEXT)[1]["text"] = "SQL or Postgres"
        v = await save(api, store, doc, 0)
        iid = item_id(store, "SQL or Postgres")
        who = user(uid="00000000-0000-0000-0000-0000000000c1")
        out = await api.confirm_structure_review(store.session(), who, JOB, 1, iid)
        assert out["revision"] == 2 and out["readiness"]["state"] == "ready"
        assert struct_state(out, iid) == "confirmed"
        (rec,) = [r for r in stored_doc(store)["structure_review"]["records"]]
        assert (rec["item_id"], rec["kind"], rec["user_id"], rec["recorded_at"]) == (iid, "confirmed", who.user_id, NOW)
        assert rec["basis"] == {"text": "SQL or Postgres", "alternatives": ["SQL", "PostgreSQL"], "experience": None}
        ev = store.audit_of("requirements_structure_confirmed")[0]
        assert ev["user_id"] == who.user_id and ev["details"]["basis"] == rec["basis"] and ev["details"]["item_id"] == iid
        shown = next(i for i in out["structure_review"]["items"] if i["item_id"] == iid)
        assert shown["record"] == {"kind": "confirmed", "user_id": who.user_id, "recorded_at": NOW}

    @pytest.mark.asyncio
    async def test_the_confirmation_request_has_no_user_or_time_fields(self):
        router = importlib.import_module("routers.job_requirements")
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            router.ConfirmStructureRequest(expected_revision=0, item_id="req_x", user_id="u", confirmed_at="t")
        assert router.ConfirmStructureRequest(expected_revision=0, item_id="req_x").item_id == "req_x"

    @pytest.mark.asyncio
    async def test_confirmation_is_refused_for_settled_unknown_unstructured_and_stale_requests(self, api):
        store = Store()
        r = seed(store, structured_job())
        ids = ids_by_text(r.requirements)
        await raises(api.confirm_structure_review(store.session(), user(), JOB, 0, ids[ALT_TEXT]), 409, "structure_not_confirmable")
        await raises(api.confirm_structure_review(store.session(), user(), JOB, 0, "req_nope"), 404, "structure_item_not_found")
        doc = client_doc(await view(api, store))
        doc["categories"]["skills"]["items"].append({"text": "Go", "importance": "preferred", "weight": None})
        item(doc, ALT_TEXT)[1]["text"] = "SQL or Postgres"
        await save(api, store, doc, 0)
        go = item_id(store, "Go")
        exc = await raises(api.confirm_structure_review(store.session(), user(), JOB, 1, go), 409, "structure_not_confirmable")
        assert exc.extra["reason"] == "no_structure"
        await raises(api.confirm_structure_review(store.session(), user(), JOB, 0, ids[ALT_TEXT]), 409, "requirements_revision_conflict")
        assert store.audit_of("requirements_structure_confirmed") == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("role", ["recruiter", "viewer", "super_admin"])
    async def test_only_admin_and_hr_manager_may_confirm_and_other_tenants_see_nothing(self, api, role):
        store = Store()
        r = seed(store, structured_job())
        iid = ids_by_text(r.requirements)[ALT_TEXT]
        await raises(api.confirm_structure_review(store.session(), user(role=role), JOB, 0, iid), 403, "forbidden")
        await raises(api.confirm_structure_review(store.session(), user(role="admin", tenant=T2), JOB, 0, iid), 404, "not_found")
        await raises(api.confirm_structure_review(store.session(), user(), "legacy-id", 0, iid), 404, "not_found")
        assert store.rows[JOB]["revision"] == 0 and store.audit == []

    @pytest.mark.asyncio
    async def test_a_legacy_job_cannot_be_confirmed(self, api):
        store = Store()
        seed(store, legacy=True)
        await raises(api.confirm_structure_review(store.session(), user(), JOB, 0, "req_x"), 409, "not_requirements_v2")

    @pytest.mark.asyncio
    async def test_a_later_wording_or_structure_change_invalidates_the_confirmation(self, api):
        store = Store()
        seed(store, structured_job())
        doc = client_doc(await view(api, store))
        item(doc, ALT_TEXT)[1]["text"] = "SQL or Postgres"
        await save(api, store, doc, 0)
        iid = item_id(store, "SQL or Postgres")
        v = await api.confirm_structure_review(store.session(), user(), JOB, 1, iid)
        # wording changes again -> the confirmation no longer applies
        doc = client_doc(v)
        item(doc, "SQL or Postgres")[1]["text"] = "SQL or Postgres 15"
        v = await save(api, store, doc, 2)
        assert struct_state(v, iid) == "needs_review" and v["readiness"]["state"] == "needs_structure_review"
        inv = store.audit_of("requirements_structure_confirmation_invalidated")
        assert [(e["details"]["item_id"], e["details"]["reason"]) for e in inv] == [(iid, "item_changed")]
        # confirm again, then change only the structure -> invalidated again, but the edit itself is a correction
        v = await api.confirm_structure_review(store.session(), user(), JOB, 3, iid)
        doc = client_doc(v)
        item(doc, "SQL or Postgres 15")[1]["alternatives"] = ["SQL", "PostgreSQL", "MariaDB"]
        v = await save(api, store, doc, 4)
        assert struct_state(v, iid) == "corrected" and v["readiness"]["state"] == "ready"
        assert len(store.audit_of("requirements_structure_confirmation_invalidated")) == 2

    @pytest.mark.asyncio
    async def test_reverting_the_wording_does_not_revive_an_invalidated_confirmation(self, api):
        store = Store()
        seed(store, structured_job())
        doc = client_doc(await view(api, store))
        item(doc, ALT_TEXT)[1]["text"] = "SQL or Postgres"
        await save(api, store, doc, 0)
        iid = item_id(store, "SQL or Postgres")
        v = await api.confirm_structure_review(store.session(), user(), JOB, 1, iid)
        doc = client_doc(v)
        item(doc, "SQL or Postgres")[1]["text"] = "something else"
        v = await save(api, store, doc, 2)
        doc = client_doc(v)
        item(doc, "something else")[1]["text"] = "SQL or Postgres"                              # back to the confirmed wording
        v = await save(api, store, doc, 3)
        assert struct_state(v, iid) == "needs_review"                                           # needs a fresh confirmation

    @pytest.mark.asyncio
    async def test_restoring_the_original_wording_exactly_settles_it_by_the_original(self, api):
        store = Store()
        seed(store, structured_job())
        doc = client_doc(await view(api, store))
        item(doc, ALT_TEXT)[1]["text"] = "SQL or Postgres"
        v = await save(api, store, doc, 0)
        assert v["readiness"]["state"] == "needs_structure_review"
        doc = client_doc(v)
        item(doc, "SQL or Postgres")[1]["text"] = ALT_TEXT
        v = await save(api, store, doc, 1)
        assert v["readiness"]["state"] == "ready" and v["edited_categories"]["skills"] is False

    # ── experience subject / duration ────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_editing_the_experience_subject_and_duration_is_a_recorded_correction(self, api):
        store = Store()
        r = seed(store, structured_job())
        iid = ids_by_text(r.requirements)[EXP_TEXT]
        before_original = copy.deepcopy(store.rows[JOB]["original_analysis"])
        doc = client_doc(await view(api, store))
        item(doc, EXP_TEXT)[1]["text"] = "7 years as a Senior Maintenance Planner"
        item(doc, "7 years as a Senior Maintenance Planner")[1]["experience"] = {"subject": "Senior Maintenance Planner", "min_years": 7}
        who = user(uid="00000000-0000-0000-0000-0000000000c2")
        out = await save(api, store, doc, 0, who)
        _, e = item(stored_doc(store), "7 years as a Senior Maintenance Planner")
        assert e["id"] == iid and e["experience"] == {"subject": "Senior Maintenance Planner", "min_years": 7}
        assert (e["origin"], e["source_text"]) == ("stated", EXP_TEXT)
        assert struct_state(out, iid) == "corrected" and out["readiness"]["state"] == "ready"
        assert out["edited_categories"]["experience"] is True
        assert store.rows[JOB]["original_analysis"] == before_original
        assert out["original"]["categories"]["experience"]["items"][0]["experience"] == {"subject": "Maintenance Planner", "min_years": 4}
        rec = stored_doc(store)["structure_review"]["records"][0]
        assert (rec["kind"], rec["user_id"], rec["recorded_at"]) == ("corrected", who.user_id, NOW)
        d = store.audit_of("requirements_structure_recorded")[0]["details"]
        assert (d["item_id"], d["kind"], d["previous_revision"], d["revision"]) == (iid, "corrected", 0, 1)

    @pytest.mark.asyncio
    async def test_duration_or_subject_alone_may_be_cleared_but_not_both(self, api):
        store = Store()
        seed(store, structured_job())
        base = client_doc(await view(api, store))
        d = copy.deepcopy(base); item(d, EXP_TEXT)[1]["experience"] = {"subject": "Planner", "min_years": None}
        assert (await save(api, store, d, 0))["readiness"]["state"] == "ready"
        d = client_doc(await view(api, store)); item(d, EXP_TEXT)[1]["experience"] = {"subject": None, "min_years": 3}
        assert (await save(api, store, d, 1))["revision"] == 2
        for bad in ({"subject": None, "min_years": None}, {"subject": "x", "min_years": -1}, {"subject": "x"},
                    {"subject": "x", "min_years": "4"}, {"subject": "", "min_years": 2}, "4 years", []):
            d = client_doc(await view(api, store)); item(d, EXP_TEXT)[1]["experience"] = bad
            exc = await raises(save(api, store, d, 2), 422, "invalid_requirements")
            assert any(i["code"] == "bad_experience" for i in exc.extra["issues"]), bad
        assert store.rows[JOB]["revision"] == 2

    @pytest.mark.asyncio
    async def test_experience_cannot_be_attached_outside_the_experience_category(self, api):
        store = Store()
        seed(store, structured_job())
        d = client_doc(await view(api, store))
        item(d, ALT_TEXT)[1]["experience"] = {"subject": "x", "min_years": 1}
        exc = await raises(save(api, store, d, 0), 422, "invalid_requirements")
        assert exc.extra["issues"][0]["code"] == "experience_outside_experience_category"

    # ── OR alternatives ──────────────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_editing_or_alternatives_is_a_recorded_correction_and_never_reinterpreted(self, api):
        store = Store()
        r = seed(store, structured_job())
        iid = ids_by_text(r.requirements)[ALT_TEXT]
        d = client_doc(await view(api, store))
        item(d, ALT_TEXT)[1]["alternatives"] = ["sql", "PostgreSQL", " MySQL "]                   # stored verbatim
        out = await save(api, store, d, 0)
        assert item(stored_doc(store), ALT_TEXT)[1]["alternatives"] == ["sql", "PostgreSQL", " MySQL "]
        assert struct_state(out, iid) == "corrected" and out["readiness"]["state"] == "ready"
        assert out["edited_categories"]["skills"] is True

    @pytest.mark.asyncio
    async def test_alternatives_need_two_entries_and_can_be_removed_or_added(self, api):
        store = Store()
        seed(store, structured_job())
        for bad in (["only"], [], ["a", ""], ["a", 3], "a or b"):
            d = client_doc(await view(api, store)); item(d, ALT_TEXT)[1]["alternatives"] = bad
            exc = await raises(save(api, store, d, 0), 422, "invalid_requirements")
            assert any(i["code"] == "bad_alternatives" for i in exc.extra["issues"]), bad
        d = client_doc(await view(api, store)); item(d, ALT_TEXT)[1]["alternatives"] = None       # no structure any more
        out = await save(api, store, d, 0)
        assert out["structure_review"]["items"][0]["item_id"] != item_id(store, ALT_TEXT) and out["readiness"]["state"] == "ready"
        d = client_doc(out); item(d, ALT_TEXT)[1]["alternatives"] = ["SQL", "PostgreSQL"]        # structure added to an item
        out = await save(api, store, d, 1)
        assert struct_state(out, item_id(store, ALT_TEXT)) == "original"                          # identical to the AI reading

    @pytest.mark.asyncio
    async def test_adding_alternatives_to_an_unstructured_item_is_a_correction(self, api):
        store = Store()
        r = seed(store, flagged_job(policy=False))
        d = client_doc(await view(api, store))
        item(d, "SQL")[1]["alternatives"] = ["SQL", "PostgreSQL"]
        out = await save(api, store, d, 0)
        assert struct_state(out, ids_by_text(r.requirements)["SQL"]) == "corrected"

    # ── new structured items ────────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_new_items_may_carry_alternatives_or_experience(self, api):
        store = Store()
        seed(store, structured_job())
        d = client_doc(await view(api, store))
        d["categories"]["skills"]["items"].append({"text": "Docker or Podman", "importance": "preferred", "weight": None,
                                                    "alternatives": ["Docker", "Podman"],
                                                    "origin": "stated", "source_text": "invented"})
        d["categories"]["experience"]["items"].append({"text": "2 years of QA", "importance": "required", "weight": 50,
                                                        "experience": {"subject": "QA", "min_years": 2}})
        item(d, EXP_TEXT)[1]["weight"] = 50
        out = await save(api, store, d, 0)
        _, alt = item(stored_doc(store), "Docker or Podman")
        _, exp = item(stored_doc(store), "2 years of QA")
        assert re.fullmatch(r"req_[0-9a-f]{12}", alt["id"]) and alt["alternatives"] == ["Docker", "Podman"]
        assert (alt["origin"], alt["source_text"]) == ("recruiter_added", None)
        assert (exp["origin"], exp["experience"]) == ("recruiter_added", {"subject": "QA", "min_years": 2})
        assert struct_state(out, alt["id"]) == "entered" and struct_state(out, exp["id"]) == "entered"
        assert out["readiness"]["state"] == "ready"
        kinds = {e["details"]["item_id"]: e["details"]["kind"] for e in store.audit_of("requirements_structure_recorded")}
        assert kinds == {alt["id"]: "entered", exp["id"]: "entered"}

    @pytest.mark.asyncio
    async def test_a_new_item_with_invalid_structure_is_refused_and_one_without_needs_no_record(self, api):
        store = Store()
        seed(store, structured_job())
        d = client_doc(await view(api, store))
        d["categories"]["skills"]["items"].append({"text": "X", "importance": "preferred", "weight": None, "experience": {"subject": "x", "min_years": 1}})
        await raises(save(api, store, d, 0), 422, "invalid_requirements")
        d = client_doc(await view(api, store))
        d["categories"]["skills"]["items"].append({"text": "Plain", "importance": "preferred", "weight": None})
        out = await save(api, store, d, 0)
        assert out["readiness"]["state"] == "ready" and not store.audit_of("requirements_structure_recorded")

    # ── forged and stale confirmations ──────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_a_forged_structure_record_with_a_matching_hash_is_discarded(self, api):
        store = Store()
        seed(store, structured_job())
        d = client_doc(await view(api, store))
        item(d, ALT_TEXT)[1]["text"] = "SQL or Postgres"
        from services.requirements_v2.structure import basis_hash
        basis = {"text": "SQL or Postgres", "alternatives": ["SQL", "PostgreSQL"], "experience": None}
        d["structure_review"] = {"records": [{"item_id": item(d, "SQL or Postgres")[1]["id"], "kind": "confirmed",
                                              "user_id": "mallory", "recorded_at": "2020-01-01T00:00:00Z",
                                              "basis": basis, "basis_hash": basis_hash(basis)}]}
        out = await save(api, store, d, 0)
        assert out["readiness"]["state"] == "needs_structure_review"
        assert "structure_review" in out["discarded_client_fields"]
        assert "structure_review" not in stored_doc(store) or stored_doc(store)["structure_review"]["records"] == []

    @pytest.mark.asyncio
    async def test_a_stale_record_replayed_after_the_wording_changed_is_discarded(self, api):
        store = Store()
        seed(store, structured_job())
        d = client_doc(await view(api, store))
        item(d, ALT_TEXT)[1]["text"] = "SQL or Postgres"
        await save(api, store, d, 0)
        iid = item_id(store, "SQL or Postgres")
        v = await api.confirm_structure_review(store.session(), user(), JOB, 1, iid)
        old_record = copy.deepcopy(stored_doc(store)["structure_review"]["records"][0])
        d = client_doc(v); item(d, "SQL or Postgres")[1]["text"] = "SQL or Postgres 16"
        await save(api, store, d, 2)                                                              # confirmation invalidated
        d = client_doc(await view(api, store)); d["structure_review"] = {"records": [old_record]}
        item(d, "SQL or Postgres 16")[1]["text"] = "SQL or Postgres 17"
        out = await save(api, store, d, 3)
        assert out["readiness"]["state"] == "needs_structure_review"

    @pytest.mark.asyncio
    async def test_a_stored_record_cannot_be_deleted_or_edited_through_a_save(self, api):
        store = Store()
        seed(store, structured_job())
        d = client_doc(await view(api, store))
        item(d, ALT_TEXT)[1]["text"] = "SQL or Postgres"
        await save(api, store, d, 0)
        v = await api.confirm_structure_review(store.session(), user(), JOB, 1, item_id(store, "SQL or Postgres"))
        d = client_doc(v)
        d["structure_review"] = {"records": []}
        item(d, "SQL or Postgres")[1]["weight"] = 100                                              # irrelevant real edit
        out = await save(api, store, d, 2)
        assert len(stored_doc(store)["structure_review"]["records"]) == 1 and out["readiness"]["state"] == "ready"

    @pytest.mark.asyncio
    async def test_source_wording_and_provenance_cannot_be_changed_by_a_structure_edit(self, api):
        store = Store()
        seed(store, structured_job())
        d = client_doc(await view(api, store))
        it = item(d, EXP_TEXT)[1]
        it["experience"] = {"subject": "Planner", "min_years": 9}
        it["source_text"], it["origin"] = "rewritten", "recruiter_added"
        out = await save(api, store, d, 0)
        _, stored = item(stored_doc(store), EXP_TEXT)
        assert (stored["source_text"], stored["origin"]) == (EXP_TEXT, "stated")
        assert any(f.endswith(".source_text") for f in out["discarded_client_fields"])

    # ── interaction with classification acknowledgments and the preferred-only confirmation ────────

    @pytest.mark.asyncio
    async def test_a_structure_edit_invalidates_the_classification_acknowledgment(self, api):
        store = Store()
        r = seed(store, preferred_structured_job(cue=None))
        wid = f"{ids_by_text(r.requirements)['Docker or Podman']}:preferred_cue_missing"
        v = await ack(api, store, wid, 0)
        assert warning_state(v, wid) == "acknowledged" and v["readiness"]["state"] == "ready"
        d = client_doc(v)
        item(d, "Docker or Podman")[1]["alternatives"] = ["Docker", "Podman", "containerd"]
        out = await save(api, store, d, 1)
        assert warning_state(out, wid) == "unresolved" and out["readiness"]["state"] == "needs_classification_review"
        inv = store.audit_of("requirements_classification_ack_invalidated")
        assert [e["details"]["reason"] for e in inv] == ["item_or_evidence_changed"]

    @pytest.mark.asyncio
    async def test_a_wording_only_edit_invalidates_the_acknowledgment_and_needs_structure_review(self, api):
        store = Store(policy="false")
        r = seed(store, preferred_structured_job(cue=None))
        wid = f"{ids_by_text(r.requirements)['Docker or Podman']}:preferred_cue_missing"
        v = await ack(api, store, wid, 0)
        d = client_doc(v)
        item(d, "Docker or Podman")[1]["text"] = "Docker, Podman or similar"
        out = await save(api, store, d, 1)
        assert warning_state(out, wid) == "unresolved"
        assert out["readiness"]["state"] == "needs_structure_review"          # blocks even when the policy is No

    @pytest.mark.asyncio
    async def test_classification_review_comes_before_structure_review_under_policy_yes(self, api):
        store = Store()
        r = seed(store, preferred_structured_job(cue=None))
        d = client_doc(await view(api, store))
        item(d, "Docker or Podman")[1]["text"] = "Docker, Podman or similar"
        out = await save(api, store, d, 0)
        assert out["readiness"]["state"] == "needs_classification_review"
        assert out["readiness"]["structure_review_item_ids"]                   # still visible

    @pytest.mark.asyncio
    async def test_a_structure_edit_invalidates_the_preferred_only_confirmation(self, api):
        store = Store()
        seed(store, preferred_only_structured_job())
        v = await api.confirm_no_score(store.session(), user(), JOB, 0)
        assert v["readiness"]["state"] == "ready" and v["readiness"]["scoring_mode"] == "none"
        d = client_doc(v)
        item(d, "Docker or Podman")[1]["alternatives"] = ["Docker", "Podman", "LXC"]
        out = await save(api, store, d, 1)
        assert out["preferred_only_confirmation"] == {"confirmed": False}
        assert out["readiness"]["state"] == "needs_confirmation"              # structure itself is settled (corrected)
        assert len(store.audit_of("requirements_preferred_only_confirmation_invalidated")) == 1

    @pytest.mark.asyncio
    async def test_a_preferred_only_job_cannot_be_confirmed_while_a_structure_review_is_pending(self, api):
        store = Store()
        seed(store, preferred_only_structured_job())
        d = client_doc(await view(api, store))
        item(d, "Docker or Podman")[1]["text"] = "Docker or Podman or LXC"
        v = await save(api, store, d, 0)
        assert v["readiness"]["state"] == "needs_structure_review"
        exc = await raises(api.confirm_no_score(store.session(), user(), JOB, 1), 409, "nothing_to_confirm")
        assert exc.extra["readiness_state"] == "needs_structure_review"
        v = await api.confirm_structure_review(store.session(), user(), JOB, 1, item_id(store, "Docker or Podman or LXC"))
        assert v["readiness"]["state"] == "needs_confirmation"
        assert (await api.confirm_no_score(store.session(), user(), JOB, 2))["readiness"]["state"] == "ready"

    @pytest.mark.asyncio
    async def test_required_preferred_and_weight_rules_are_unchanged_by_structure_edits(self, api):
        store = Store()
        seed(store, structured_job())
        d = client_doc(await view(api, store))
        item(d, ALT_TEXT)[1]["alternatives"] = ["SQL", "PostgreSQL", "Oracle"]
        item(d, ALT_TEXT)[1]["weight"] = 60                                  # required skills weights must still total 100
        await raises(save(api, store, d, 0), 422, "invalid_requirements")
        d = client_doc(await view(api, store))
        item(d, ALT_TEXT)[1]["alternatives"] = ["SQL", "PostgreSQL", "Oracle"]
        out = await save(api, store, d, 0)
        _, a = item(out["requirements"], ALT_TEXT)
        assert (a["importance"], a["weight"]) == ("required", 100)

    @pytest.mark.asyncio
    async def test_moving_an_item_between_categories_is_still_refused(self, api):
        store = Store()
        seed(store, structured_job())
        d = client_doc(await view(api, store))
        moved = d["categories"]["skills"]["items"].pop(0)
        d["categories"]["domain_knowledge"]["items"].append(moved)
        exc = await raises(save(api, store, d, 0), 422, "invalid_requirements")
        assert exc.extra["issues"][0]["code"] == "item_category_change_unsupported"

    # ── concurrency and atomicity ───────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_two_concurrent_confirmations_at_one_revision_cannot_both_win(self, api):
        store = Store()
        seed(store, structured_job())
        d = client_doc(await view(api, store))
        item(d, ALT_TEXT)[1]["text"] = "SQL or Postgres"
        item(d, EXP_TEXT)[1]["text"] = "8 years as a Maintenance Planner"
        await save(api, store, d, 0)
        a, b = item_id(store, "SQL or Postgres"), item_id(store, "8 years as a Maintenance Planner")
        res = await asyncio.gather(api.confirm_structure_review(store.session(), user(), JOB, 1, a),
                                   api.confirm_structure_review(store.session(), user(), JOB, 1, b), return_exceptions=True)
        assert sum(isinstance(r, dict) for r in res) == 1
        assert sum(isinstance(r, api.ApiError) and r.code == "requirements_revision_conflict" for r in res) == 1
        assert len(stored_doc(store)["structure_review"]["records"]) == 1 and store.rows[JOB]["revision"] == 2

    @pytest.mark.asyncio
    async def test_a_confirmation_racing_a_wording_edit_cannot_confirm_stale_wording(self, api):
        store = Store()
        seed(store, structured_job())
        d = client_doc(await view(api, store))
        item(d, ALT_TEXT)[1]["text"] = "SQL or Postgres"
        await save(api, store, d, 0)
        iid = item_id(store, "SQL or Postgres")
        edit = client_doc(await view(api, store)); item(edit, "SQL or Postgres")[1]["text"] = "SQL or Oracle"
        res = await asyncio.gather(save(api, store, edit, 1),
                                   api.confirm_structure_review(store.session(), user(), JOB, 1, iid), return_exceptions=True)
        assert sum(isinstance(r, dict) for r in res) == 1
        v = await view(api, store)
        # whichever won, no record may describe wording that is not the stored wording
        for r in stored_doc(store).get("structure_review", {"records": []})["records"]:
            assert r["basis"]["text"] == item(stored_doc(store), r["basis"]["text"])[1]["text"]
        assert v["revision"] == 2

    @pytest.mark.asyncio
    async def test_if_the_audit_row_cannot_be_written_the_confirmation_is_not_stored(self, api):
        store = Store()
        seed(store, structured_job())
        d = client_doc(await view(api, store))
        item(d, ALT_TEXT)[1]["text"] = "SQL or Postgres"
        await save(api, store, d, 0)
        iid = item_id(store, "SQL or Postgres")
        before = copy.deepcopy(store.rows[JOB])
        store.fail_audit = True
        with pytest.raises(RuntimeError):
            await api.confirm_structure_review(store.session(), user(), JOB, 1, iid)
        assert store.rows[JOB] == before and not store.lock.locked()

    @pytest.mark.asyncio
    async def test_the_original_snapshot_survives_every_structure_operation(self, api):
        store = Store()
        seed(store, structured_job())
        before = copy.deepcopy(store.rows[JOB]["original_analysis"])
        d = client_doc(await view(api, store))
        item(d, ALT_TEXT)[1]["text"] = "SQL or Postgres"
        item(d, EXP_TEXT)[1]["experience"] = {"subject": "Planner", "min_years": 2}
        d["categories"]["skills"]["items"].append({"text": "New", "importance": "preferred", "weight": None, "alternatives": ["a", "b"]})
        v = await save(api, store, d, 0)
        await api.confirm_structure_review(store.session(), user(), JOB, 1, item_id(store, "SQL or Postgres"))
        assert store.rows[JOB]["original_analysis"] == before
        assert (await view(api, store))["original"]["categories"] == before["requirements"]["categories"]


# ══ the classification-warning lifecycle through the API ════════════════════════════════════════════════════════

def wids(r):
    ids = ids_by_text(r.requirements)
    return f"{ids['Docker']}:preferred_cue_missing", f"{ids['Kubernetes']}:preferred_cue_not_linked_to_item"


class TestAcknowledgmentLifecycle:

    @pytest.mark.asyncio
    async def test_each_warning_is_acknowledged_separately_and_both_make_the_job_ready(self, api):
        store = Store()
        r = seed(store)
        w1, w2 = wids(r)
        v = await ack(api, store, w1, 0)
        assert warning_state(v, w1) == "acknowledged" and warning_state(v, w2) == "unresolved"
        assert v["readiness"]["state"] == "needs_classification_review"
        v = await ack(api, store, w2, 1)
        assert v["readiness"]["state"] == "ready" and v["readiness"]["scoring_mode"] == "weighted"
        a = store.audit_of("requirements_classification_acknowledged")
        assert [x["details"]["warning_id"] for x in a] == [w1, w2]
        assert a[0]["user_id"] == user().user_id and a[0]["details"]["revision"] == 1

    @pytest.mark.asyncio
    async def test_unknown_resolved_and_repeated_acknowledgments_are_refused(self, api):
        store = Store()
        r = seed(store)
        w1, _ = wids(r)
        await raises(ack(api, store, "req_nope:preferred_cue_missing", 0), 404, "classification_warning_not_found")
        await ack(api, store, w1, 0)
        exc = await raises(ack(api, store, w1, 1), 409, "classification_warning_not_acknowledgeable")
        assert exc.extra["reason"] == "already_acknowledged"
        assert store.rows[JOB]["revision"] == 1

    @pytest.mark.asyncio
    async def test_a_stale_revision_cannot_acknowledge(self, api):
        store = Store()
        r = seed(store)
        w1, w2 = wids(r)
        await ack(api, store, w1, 0)
        await raises(ack(api, store, w2, 0), 409, "requirements_revision_conflict")

    @pytest.mark.asyncio
    async def test_editing_the_wording_of_an_acknowledged_item_drops_the_acknowledgment(self, api):
        store = Store()
        r = seed(store)
        w1, w2 = wids(r)
        await ack(api, store, w1, 0)
        v = await ack(api, store, w2, 1)
        doc = client_doc(v)
        item(doc, "Docker")[1]["text"] = "Docker Swarm"
        v = await save(api, store, doc, 2)
        assert warning_state(v, w1) == "unresolved" and warning_state(v, w2) == "acknowledged"
        assert v["readiness"]["state"] == "needs_classification_review"
        inv = store.audit_of("requirements_classification_ack_invalidated")
        assert [(x["details"]["warning_id"], x["details"]["reason"]) for x in inv] == [(w1, "item_or_evidence_changed")]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("policy", ["true", "false"], ids=["policy-yes", "policy-no"])
    @pytest.mark.parametrize("acknowledged", [False, True], ids=["unacknowledged", "acknowledged"])
    async def test_preferred_required_preferred_reopens_the_warning(self, api, policy, acknowledged):
        store = Store(policy=policy)
        r = seed(store)
        w1, _ = wids(r)
        rev = 0
        if acknowledged:
            await ack(api, store, w1, rev)
            rev += 1
        v = await view(api, store)
        doc = client_doc(v)
        to_required(doc, "Docker")
        v = await save(api, store, doc, rev)                                          # Preferred -> Required
        rev += 1
        assert warning_state(v, w1) == "inactive" and w1 not in v["readiness"]["open_warning_ids"]
        assert all(a["warning_id"] != w1 for a in
                   store.rows[JOB]["analysis"]["requirements"]["classification_review"]["acknowledgments"])
        doc = client_doc(v)
        to_preferred(doc, "Docker")
        v = await save(api, store, doc, rev)                                          # Required -> Preferred
        assert warning_state(v, w1) == "unresolved"                                   # reopened, NOT re-acknowledged
        assert w1 in v["readiness"]["unresolved_warning_ids"]
        if policy == "true":
            assert v["readiness"]["state"] == "needs_classification_review"
        else:
            assert v["readiness"]["state"] == "ready" and v["readiness"]["can_proceed"]       # visible, not blocking
        if acknowledged:
            reasons = [x["details"]["reason"] for x in store.audit_of("requirements_classification_ack_invalidated")]
            assert reasons == ["warning_inactive"]

    @pytest.mark.asyncio
    async def test_under_policy_no_warnings_stay_visible_without_blocking(self, api):
        store = Store(policy="false")
        r = seed(store)
        v = await view(api, store)
        assert v["readiness"]["state"] == "ready" and v["readiness"]["can_proceed"]
        assert len(v["readiness"]["unresolved_warning_ids"]) == 2 and len(v["classification_warnings"]) == 2
        assert v["classification_policy"]["require_acknowledgment"] is False
        v = await ack(api, store, wids(r)[0], 0)                                     # still allowed, still recorded
        assert warning_state(v, wids(r)[0]) == "acknowledged"

    @pytest.mark.asyncio
    async def test_removing_an_item_resolves_its_warning_permanently(self, api):
        store = Store()
        r = seed(store)
        w1, w2 = wids(r)
        await ack(api, store, w1, 0)
        doc = client_doc(await view(api, store))
        doc["categories"]["skills"]["items"] = [i for i in doc["categories"]["skills"]["items"] if i["text"] != "Docker"]
        v = await save(api, store, doc, 1)
        assert warning_state(v, w1) == "resolved" and w1 not in v["readiness"]["open_warning_ids"]
        assert [x["details"]["warning_id"] for x in store.audit_of("requirements_classification_warning_resolved")] == [w1]
        await raises(ack(api, store, w1, 2), 409, "classification_warning_not_acknowledgeable")

    @pytest.mark.asyncio
    async def test_the_setting_is_read_for_every_request_and_fails_closed(self, api):
        store = Store(policy="false")
        seed(store)
        assert (await view(api, store))["readiness"]["state"] == "ready"
        store.config[POLICY_KEY] = "true"
        assert (await view(api, store))["readiness"]["state"] == "needs_classification_review"
        for junk in ("maybe", "", "  "):
            store.config[POLICY_KEY] = junk
            assert (await view(api, store))["classification_policy"]["require_acknowledgment"] is True
        del store.config[POLICY_KEY]
        assert (await view(api, store))["classification_policy"]["require_acknowledgment"] is True


# ══ preferred-only confirmation ════════════════════════════════════════════════════════════════════════════════

class TestPreferredOnlyConfirmation:

    @pytest.mark.asyncio
    async def test_confirming_makes_a_preferred_only_job_ready_without_a_score(self, api):
        store = Store()
        seed(store, preferred_only_job())
        v = await view(api, store)
        assert v["readiness"]["state"] == "needs_confirmation"
        out = await api.confirm_no_score(store.session(), user(), JOB, 0)
        assert out["readiness"]["state"] == "ready" and out["readiness"]["scoring_mode"] == "none"
        assert out["preferred_only_confirmation"] == {"confirmed": True, "user_id": user().user_id, "confirmed_at": NOW}
        assert out["revision"] == 1
        assert all(i["weight"] is None for i in out["requirements"]["categories"]["skills"]["items"])
        assert sum(c["weight"] for c in out["requirements"]["categories"].values()) == 0
        ev = store.audit_of("requirements_preferred_only_confirmed")
        assert len(ev) == 1 and ev[0]["user_id"] == user().user_id

    @pytest.mark.asyncio
    async def test_confirming_twice_or_a_weighted_job_is_refused(self, api):
        store = Store()
        seed(store, preferred_only_job())
        await api.confirm_no_score(store.session(), user(), JOB, 0)
        await raises(api.confirm_no_score(store.session(), user(), JOB, 1), 409, "nothing_to_confirm")
        weighted = Store(policy="false")
        seed(weighted)
        exc = await raises(api.confirm_no_score(weighted.session(), user(), JOB, 0), 409, "nothing_to_confirm")
        assert exc.extra["readiness_state"] == "ready"

    @pytest.mark.asyncio
    async def test_a_stale_revision_cannot_confirm(self, api):
        store = Store()
        seed(store, preferred_only_job())
        doc = client_doc(await view(api, store))
        item(doc, "SQL")[1]["text"] = "SQL 2"
        await save(api, store, doc, 0)
        await raises(api.confirm_no_score(store.session(), user(), JOB, 0), 409, "requirements_revision_conflict")

    @pytest.mark.asyncio
    async def test_a_forged_confirmation_in_a_save_is_discarded_even_with_a_matching_hash(self, api):
        store = Store()
        r = seed(store, preferred_only_job())
        doc = client_doc(await view(api, store))
        doc["scoring_confirmation"] = confirm_no_numeric_score(
            r.requirements, user_id="mallory", confirmed_at="2020-01-01T00:00:00Z")["scoring_confirmation"]
        item(doc, "Docker")[1]["weight"] = None
        out = await save(api, store, doc, 0)
        assert out["changed"] is False                                                # nothing client-side was kept
        assert store.rows[JOB]["analysis"]["requirements"]["scoring_confirmation"] is None
        assert out["readiness"]["state"] == "needs_confirmation"

    @pytest.mark.asyncio
    async def test_a_stored_confirmation_survives_only_while_the_items_are_unchanged(self, api):
        store = Store()
        seed(store, preferred_only_job())
        v = await api.confirm_no_score(store.session(), user(), JOB, 0)
        # a save that changes nothing material keeps it
        kept = await save(api, store, client_doc(v), 1)
        assert kept["preferred_only_confirmation"]["confirmed"] is True and kept["changed"] is False
        # a wording change drops it, and says so in the audit log
        doc = client_doc(v)
        item(doc, "SQL")[1]["text"] = "SQL 2"
        out = await save(api, store, doc, 1)
        assert out["preferred_only_confirmation"] == {"confirmed": False} and out["readiness"]["state"] == "needs_confirmation"
        assert len(store.audit_of("requirements_preferred_only_confirmation_invalidated")) == 1

    @pytest.mark.asyncio
    async def test_adding_a_required_item_makes_the_confirmation_inapplicable(self, api):
        store = Store()
        seed(store, preferred_only_job())
        v = await api.confirm_no_score(store.session(), user(), JOB, 0)
        doc = client_doc(v)
        doc["categories"]["skills"]["items"].append({"text": "Python", "importance": "required", "weight": 100})
        doc["categories"]["skills"]["weight"] = 100
        out = await save(api, store, doc, 1)
        assert out["preferred_only_confirmation"] == {"confirmed": False}
        assert out["readiness"]["state"] == "ready" and out["readiness"]["scoring_mode"] == "weighted"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("policy,blocked", [("true", True), ("false", False)], ids=["policy-yes", "policy-no"])
    async def test_unresolved_classification_warnings_block_confirmation_only_under_policy_yes(self, api, policy, blocked):
        store = Store(policy=policy)
        r = extract("Requirements:\n- SQL is a plus\n- Docker\n",
                    raw_item("SQL", "SQL is a plus", "preferred", "is a plus"),
                    raw_item("Docker", "Docker", "preferred", None))
        assert r.requirements and r.requirements["classification_review"]["warnings"]
        seed(store, r)
        coro = api.confirm_no_score(store.session(), user(), JOB, 0)
        if blocked:
            exc = await raises(coro, 409, "nothing_to_confirm")
            assert exc.extra["readiness_state"] == "needs_classification_review"
        else:
            out = await coro
            assert out["readiness"]["state"] == "ready" and out["classification_warnings"][0]["state"] == "unresolved"


# ══ the platform-wide setting ══════════════════════════════════════════════════════════════════════════════════

class TestPolicySetting:

    def test_migration_107_is_additive_and_seeds_the_setting(self):
        sql = (BACKEND / "db" / "migrations" / "107_requirements_v2_revision.sql").read_text(encoding="utf-8")
        body = "\n".join(l.split("--")[0] for l in sql.splitlines())
        assert "ADD COLUMN IF NOT EXISTS requirements_revision INTEGER NOT NULL DEFAULT 0" in body
        assert "ADD COLUMN IF NOT EXISTS requirements_retired_item_ids JSONB NOT NULL DEFAULT '[]'" in body
        assert "ON CONFLICT (key) DO NOTHING" in body
        assert "'job_analysis.require_classification_acknowledgment', 'true', 'boolean'" in " ".join(body.split())
        assert not re.search(r"\bDROP\s+(TABLE|COLUMN)\b|\bDELETE\b|\bTRUNCATE\b|\bUPDATE\s+\w+\s+SET\b", body, re.I)
        assert body.count("BEGIN;") == 1 and body.count("COMMIT;") == 1

    def test_the_key_is_the_service_constant(self):
        assert POLICY_KEY == "job_analysis.require_classification_acknowledgment"

    def test_only_super_admin_can_change_it_through_the_existing_config_endpoint(self):
        pc = importlib.import_module("routers.platform_config")
        route = next(r for r in pc.router.routes if r.path == "/admin/platform-config/{key}" and "PUT" in r.methods)
        assert route.dependencies, "PUT /admin/platform-config/{key} must stay super_admin-only"
        from auth.dependencies import RequireSuperAdmin
        assert [d.dependency.__qualname__ for d in route.dependencies] == [RequireSuperAdmin.dependency.__qualname__]

    @pytest.mark.asyncio
    async def test_a_change_is_audited_with_old_and_new_value(self):
        pc = importlib.import_module("routers.platform_config")

        class Db(Session):
            async def execute(self, stmt, params=None):
                sql = str(stmt)
                if "SELECT key, type, editable, value FROM system_config" in sql:
                    return Result(first={"key": POLICY_KEY, "type": "boolean", "editable": True, "value": "true"})
                return await super().execute(stmt, params)

        store = Store()
        db = Db(store)
        who = user(role="super_admin")
        out = await pc.update_platform_config(POLICY_KEY, pc.UpdateConfigValueRequest(value="false"), who, db)
        assert out == {"success": True, "key": POLICY_KEY, "value": "false"}
        assert db.commits == 1
        ev = store.audit_of("requirements_classification_policy_changed")
        assert len(ev) == 1 and ev[0]["details"] == {"key": POLICY_KEY, "old_value": "true", "new_value": "false"}

    @pytest.mark.asyncio
    async def test_a_non_boolean_value_is_refused(self):
        pc = importlib.import_module("routers.platform_config")

        class Db(Session):
            async def execute(self, stmt, params=None):
                if "SELECT key, type, editable, value FROM system_config" in str(stmt):
                    return Result(first={"key": POLICY_KEY, "type": "boolean", "editable": True, "value": "true"})
                return await super().execute(stmt, params)

        store = Store()
        with pytest.raises(HTTPException) as ei:
            await pc.update_platform_config(POLICY_KEY, pc.UpdateConfigValueRequest(value="maybe"), user(role="super_admin"), Db(store))
        assert ei.value.status_code == 422 and store.audit == []

    @pytest.mark.asyncio
    async def test_other_keys_are_not_audited_by_this_hook(self):
        pc = importlib.import_module("routers.platform_config")

        class Db(Session):
            async def execute(self, stmt, params=None):
                if "SELECT key, type, editable, value FROM system_config" in str(stmt):
                    return Result(first={"key": "qualified_threshold", "type": "number", "editable": True, "value": "70"})
                return await super().execute(stmt, params)

        store = Store()
        await pc.update_platform_config("qualified_threshold", pc.UpdateConfigValueRequest(value="75"), user(role="super_admin"), Db(store))
        assert store.audit == []

    def test_there_is_no_tenant_level_override_in_the_api_code(self):
        src = (BACKEND / "services" / "requirements_api.py").read_text(encoding="utf-8")
        assert "FROM system_config WHERE key = :k" in src
        assert "tenant_features" not in src and "tenant_config" not in src and "tenants " not in src


# ══ legacy behavior and the scoring guard are untouched ═════════════════════════════════════════════════════════

class TestLegacyAndGuards:

    def test_v2_scoring_is_still_unsupported_and_a_saved_v2_job_is_still_refused(self):
        assert REQUIREMENTS_V2_SCORING_SUPPORTED is False
        analysis = {"requirements": {"schema_version": 2, "categories": {}}}
        with pytest.raises(UnsupportedEvaluationError):
            assert_job_evaluable(entry="x", marker=None, analysis_json=analysis, job_id=JOB)

    def test_the_legacy_edit_endpoints_still_refuse_v2_jobs(self):
        src = (BACKEND / "routers" / "jobs.py").read_text(encoding="utf-8")
        for needle in ('"PUT /jobs/{id}/criteria"', '"PUT /jobs/{id}/criteria/content"',
                       '"PUT /jobs/{id}/criteria/qualifying-context"',
                       '"POST /jobs/{id}/criteria/qualifying-context/confirm"', '"POST /jobs/{id}/criteria/retry"'):
            assert f"_reject_requirements_v2(db, job_id, {needle})" in src

    def test_the_api_does_not_import_scoring_or_extraction_paths(self):
        src = (BACKEND / "services" / "requirements_api.py").read_text(encoding="utf-8")
        for forbidden in ("ai_service", "criteria_worker", "deterministic_scoring", "cv_score", "llm_provider",
                          "requirements_v2.extraction"):
            assert forbidden not in src
        wiring = (BACKEND / "routers" / "job_requirements.py").read_text(encoding="utf-8")
        assert "extract_criteria_task" not in wiring and "create_job" not in wiring

    def test_no_other_production_module_imports_the_requirements_package_except_the_api_layer(self):
        offenders = []
        for path in BACKEND.rglob("*.py"):
            rel = path.relative_to(BACKEND).as_posix()
            if rel.startswith(("tests/", "services/requirements_v2/", "parser_candidates/", "venv")) or "/site-packages/" in rel:
                continue
            if rel in {"scripts/requirements_v2_extraction_eval.py", "scripts/_gen_benchmark_cases_md.py", "scripts/requirements_v2_extraction_run.py",
                  "scripts/requirements_v2_extraction_compare.py",
                  "scripts/requirements_v2_injection_guard_replay.py"}:
                continue   # offline benchmark tooling: no model, network or database (pinned by test_requirements_v2_benchmark_cases)
            text_ = path.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"^\s*(from|import)\s+services\.requirements_v2\b", text_, re.M):
                offenders.append(rel)
        assert offenders == ["services/requirements_api.py"], offenders
