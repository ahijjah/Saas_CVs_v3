"""Requirements-v2 evaluation guard: marker/shape detection, entry-point vs legacy-component semantics, the SQL helpers,
and the legacy components that must refuse a v2 job. Synthetic data only; no database, no model."""
from __future__ import annotations

import asyncio
import re

import pytest

from services import requirements_guard as g
from services.requirements_guard import (
    REASON_CODE, UnsupportedEvaluationError, analysis_declares_v2, assert_job_evaluable, assert_legacy_component,
    ensure_job_evaluable, ensure_job_legacy, is_requirements_v2, load_job_marker, marker_is_set,
)

LEGACY_ANALYSIS = {
    "skills": {"required": ["Python"], "preferred": []},
    "experience": {"minimum_years": 3, "relevant_roles": [], "key_responsibilities": []},
    "education": {"minimum_level": "None", "fields_of_study": []},
    "certifications": [], "domain_knowledge": [], "other_requirements": [],
    "scoring_weights": {"skills": 100},
}
V2_ANALYSIS = {"requirements": {"schema_version": 2, "categories": {}}}


def run(coro):
    return asyncio.run(coro)


# ── defaults ──────────────────────────────────────────────────────────────────

def test_v2_evaluation_support_is_disabled_by_default():
    assert g.REQUIREMENTS_V2_SCORING_SUPPORTED is False
    assert g.STOPPED_REASON == "evaluation_unsupported"


# ── detection ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("marker,expected", [(None, False), ("", False), (2, True), ("2", True), (3, True),
                                              ("anything", True), (0, True)])
def test_any_non_null_marker_is_treated_as_v2_fail_closed(marker, expected):
    assert marker_is_set(marker) is expected
    assert is_requirements_v2(marker, None) is expected


def test_legacy_jobs_are_not_v2():
    assert not is_requirements_v2(None, LEGACY_ANALYSIS)
    assert not is_requirements_v2(None, None)
    assert not is_requirements_v2(None, {})
    assert not is_requirements_v2(None, "not a dict")
    assert not is_requirements_v2(None, [])


@pytest.mark.parametrize("analysis", [
    {"requirements": []},                                   # a stray key from a model answer is not a v2 block
    {"requirements": "x"},
    {"requirements": {"schema_version": 1}},
    {"requirements": {}},
    {"requirements": None},
])
def test_stray_requirements_key_is_not_mistaken_for_v2(analysis):
    assert analysis_declares_v2(analysis) is False


def test_v2_block_in_the_analysis_is_detected_even_without_a_marker():
    assert analysis_declares_v2(V2_ANALYSIS) and is_requirements_v2(None, V2_ANALYSIS)


# ── entry points vs legacy components ─────────────────────────────────────────

def test_entry_point_guard_passes_legacy_and_refuses_v2_with_explicit_reason():
    assert_job_evaluable(entry="x", marker=None, analysis_json=LEGACY_ANALYSIS, job_id="j")
    for kwargs in ({"marker": 2}, {"analysis_json": V2_ANALYSIS}):
        with pytest.raises(UnsupportedEvaluationError) as e:
            assert_job_evaluable(entry="score_cv_task", job_id="job-1", **kwargs)
        assert e.value.code == REASON_CODE and "job-1" in e.value.message and "score_cv_task" in e.value.message
        assert REASON_CODE in str(e.value)


def test_legacy_component_guard_always_refuses_v2_even_if_v2_evaluation_becomes_supported(monkeypatch):
    monkeypatch.setattr(g, "REQUIREMENTS_V2_SCORING_SUPPORTED", True)
    assert_job_evaluable(entry="entry", marker=2)                       # entry points would let it through ...
    with pytest.raises(UnsupportedEvaluationError):                     # ... legacy components still refuse
        assert_legacy_component(component="CriteriaMatchEngine.match", marker=2)
    with pytest.raises(UnsupportedEvaluationError):
        assert_legacy_component(component="LLMCriteriaMapper.assess", analysis_json=V2_ANALYSIS)


def test_legacy_component_guard_passes_legacy_inputs():
    assert_legacy_component(component="c", marker=None, analysis_json=LEGACY_ANALYSIS)
    assert_legacy_component(component="c")


# ── SQL helpers ───────────────────────────────────────────────────────────────

def test_marker_is_read_through_to_jsonb_so_it_works_before_the_column_exists():
    assert g.MARKER_SQL == "to_jsonb(jc) ->> 'requirements_schema_version'"
    # the column is never referenced directly outside the to_jsonb() lookup
    for sql in (g._LOAD_SQL, g.IS_V2_SQL):
        stripped = sql.replace(g.MARKER_SQL, "")
        assert "requirements_schema_version" not in re.sub(r"AS requirements_schema_version", "", stripped)


def test_is_v2_predicate_checks_the_marker_and_the_analysis_shape():
    assert "IS NOT NULL" in g.IS_V2_SQL and "schema_version" in g.IS_V2_SQL and "jc.analysis_json" in g.IS_V2_SQL


class _Rows:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _FakeDB:
    def __init__(self, row):
        self.row, self.sql = row, []

    async def execute(self, stmt, params=None):
        self.sql.append((str(stmt), dict(params or {})))
        return _Rows(self.row)


def test_load_job_marker_and_ensure_helpers():
    v2 = _FakeDB({"requirements_schema_version": "2", "analysis_json": None})
    assert run(load_job_marker(v2, "job-1")) == ("2", None)
    assert v2.sql[0][1] == {"jid": "job-1"}
    with pytest.raises(UnsupportedEvaluationError):
        run(ensure_job_evaluable(v2, "job-1", "bulk_import"))
    with pytest.raises(UnsupportedEvaluationError):
        run(ensure_job_legacy(v2, "job-1", "PUT /criteria"))

    legacy = _FakeDB({"requirements_schema_version": None, "analysis_json": LEGACY_ANALYSIS})
    run(ensure_job_evaluable(legacy, "j", "x"))
    run(ensure_job_legacy(legacy, "j", "x"))

    missing = _FakeDB(None)
    assert run(load_job_marker(missing, "j")) == (None, None)
    run(ensure_job_evaluable(missing, "j", "x"))                        # no criteria row: unchanged legacy behavior


def test_ensure_helpers_work_with_rows_that_lack_the_marker_key():
    """Existing mocked rows (and a database without the column) only carry analysis_json."""
    db = _FakeDB({"analysis_json": LEGACY_ANALYSIS})
    assert run(load_job_marker(db, "j")) == (None, LEGACY_ANALYSIS)
    run(ensure_job_evaluable(db, "j", "x"))


# ── legacy components refuse v2 ───────────────────────────────────────────────

def test_rule_matcher_refuses_a_v2_job():
    from services.criteria_matcher import CriteriaMatchEngine
    with pytest.raises(UnsupportedEvaluationError) as e:
        CriteriaMatchEngine().match(cv_facts=None, criteria=V2_ANALYSIS, job_id="job-9")
    assert "CriteriaMatchEngine.match" in e.value.message and "job-9" in e.value.message


def test_llm_mapper_refuses_a_v2_job_before_any_database_or_model_work():
    from services.llm_criteria_mapper import LLMCriteriaMapper

    class _Boom:
        def __getattr__(self, name):
            raise AssertionError("the database must not be touched")

    with pytest.raises(UnsupportedEvaluationError) as e:
        run(LLMCriteriaMapper().assess(cv_facts=None, analysis_json=V2_ANALYSIS, raw_cv_text="x",
                                       application_id="a", job_id="job-9", db=_Boom()))
    assert "LLMCriteriaMapper.assess" in e.value.message


def test_gatekeeper_refuses_a_v2_job_before_any_text_processing():
    from services.local_processor import run_gatekeeper
    with pytest.raises(UnsupportedEvaluationError) as e:
        run_gatekeeper("cv", "jd", ["python"], requirements_schema_version=2)
    assert "run_gatekeeper" in e.value.message


def test_legacy_components_still_accept_legacy_inputs():
    from services.criteria_matcher import CriteriaMatchEngine
    from services.cv_evidence import CVFactsExtractor
    facts = CVFactsExtractor().extract("Python developer. " * 10)
    result = CriteriaMatchEngine().match(facts, LEGACY_ANALYSIS, "a", "j")
    assert result.job_id == "j"


# ── scripts skip v2 jobs explicitly ───────────────────────────────────────────

def test_legacy_scripts_embed_the_v2_predicate():
    import importlib
    import sys
    backend = __import__("pathlib").Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(backend / "scripts"))
    try:
        pop = importlib.import_module("inspect_experience_criteria_population")
        job = importlib.import_module("inspect_job_experience_criteria")
    finally:
        sys.path.remove(str(backend / "scripts"))
    for sql in (pop.JOBS_SQL, job.JOBS_SQL):
        assert g.IS_V2_SQL in sql and "COALESCE" in sql


def test_other_script_sources_reference_the_guard():
    backend = __import__("pathlib").Path(__file__).resolve().parent.parent / "scripts"
    for name in ("backfill_deterministic_scores.py", "analyze_real_jobs_issue12.py",
                 "p002a_offline_compare.py", "p002a_phase3_cases.py"):
        src = (backend / name).read_text(encoding="utf-8")
        assert "requirements_guard" in src, name
