"""Production pipeline service (services/requirements_pipeline) versus the approved offline pipeline (parser_candidates/requirements_v2_pipeline_1):
source parity, behaviour parity on all 48 stored responses and on edit lifecycles, record integrity, the view modes (guarded / compatibility /
invalid record) and the import boundaries. No model, network or database."""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
import pathlib
import importlib
import sys
from unittest.mock import patch

import pytest

import test_p001_scoring_path as p001  # noqa: E402  (restores the real sqlalchemy / config modules other tests stub)
from parser_candidates.requirements_v2_pipeline_1 import evaluate as candidate_evaluate
from parser_candidates.requirements_v2_pipeline_1 import extract, reconcile as candidate_reconcile
from services import requirements_pipeline as pipe
from services.requirements_pipeline import core
from services.requirements_v2.editing import remove_item, set_category_weight
from services.requirements_v2.weights import equalize_category

_API_PRIVATE = "req_api_private_for_pipeline_service_tests"


class _Api:
    """services/requirements_api.py loaded ONCE under a private module name with the real sqlalchemy (other test modules leave stand-ins for it in
    sys.modules, and the editing-API tests bind `text` to whatever is there when THEY first import the service; this file therefore never puts
    `services.requirements_api` into sys.modules)."""
    _mod = None

    def __getattr__(self, name):
        if _Api._mod is None:
            with patch.dict(sys.modules, p001._REAL_MODULES):
                spec = importlib.util.spec_from_file_location(_API_PRIVATE, BACKEND / "services" / "requirements_api.py")
                mod = importlib.util.module_from_spec(spec)
                sys.modules[_API_PRIVATE] = mod
                spec.loader.exec_module(mod)
            sys.modules.pop(_API_PRIVATE, None)
            _Api._mod = mod
        return getattr(_Api._mod, name)


api = _Api()


BACKEND = pathlib.Path(__file__).resolve().parent.parent
FIX = json.loads((BACKEND / "tests" / "fixtures" / "requirements_v2_injection_guard" / "b06_v22_run1.json").read_text(encoding="utf-8"))
CONFLICT = "PostgreSQL is marked as optional, which conflicts with its inclusion as a required skill."
POLICIES = (True, False)
AT = "2026-01-01T00:00:00Z"

COPIES = {
    "injection.py": "parser_candidates/requirements_v2_injection_guard_1/guard.py",
    "split_or.py": "parser_candidates/requirements_v2_split_or_guard_1/guard.py",
    "warning_adapter.py": "parser_candidates/requirements_v2_warning_adapter_1/adapter.py",
    "text.py": "services/requirements_v2/extraction/text.py",
}
MAPPING = [("services.requirements_v2.extraction.text", "services.requirements_pipeline.text"),
           ("parser_candidates.requirements_v2_injection_guard_1.guard", "services.requirements_pipeline.injection"),
           ("parser_candidates.requirements_v2_injection_guard_1", "services.requirements_pipeline.injection"),
           ("parser_candidates.requirements_v2_split_or_guard_1", "services.requirements_pipeline.split_or"),
           ("parser_candidates.requirements_v2_warning_adapter_1", "services.requirements_pipeline.warning_adapter")]


def combined():
    raw = json.loads(FIX["raw"])
    raw["warnings"] = [CONFLICT]
    return extract(FIX["jd"], json.dumps(raw), finish_reason="stop", extraction_prompt={"version": "criteria_extraction_v2-2", "sha256": "40ea678b"})


# ══ source parity and import boundaries ═══════════════════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("name,source", COPIES.items())
def test_production_copy_is_the_approved_source_apart_from_import_paths(name, source):
    prod = (BACKEND / "services" / "requirements_pipeline" / name).read_text(encoding="utf-8")
    first, _, rest = prod.partition("\n")
    assert first.startswith("# PRODUCTION COPY of " + source)
    expected = (BACKEND / source).read_text(encoding="utf-8")
    for a, b in MAPPING:
        expected = expected.replace(a, b)
    assert rest == expected


def _layer_core_text() -> str:
    return (BACKEND / "services" / "requirements_pipeline" / "core.py").read_text(encoding="utf-8")


def test_the_derived_view_and_record_reconcile_functions_are_the_approved_pipelines():
    def funcs(path):
        tree = ast.parse((BACKEND / path).read_text(encoding="utf-8"))
        return {n.name: ast.dump(n) for n in tree.body if isinstance(n, ast.FunctionDef)}
    cand, prod = funcs("parser_candidates/requirements_v2_pipeline_1/pipeline.py"), funcs("services/requirements_pipeline/core.py")
    for name in ("_issue_dicts", "_readiness_dict", "_sha", "_plain", "_compose", "_issues", "evaluate", "_review_events"):
        assert cand[name] == prod[name], name


def test_production_pipeline_imports_no_candidate_extraction_model_database_or_scoring_code():
    bad = []
    for path in (BACKEND / "services" / "requirements_pipeline").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            for n in names:
                if n.startswith(("parser_candidates", "services.requirements_v2.extraction", "database", "workers", "routers", "openai", "httpx", "requests",
                                 "sqlalchemy")) or n in ("services.ai_service", "services.audit_service", "services.requirements_guard"):
                    # the injection guard lazily imports security_detection (patterns only) inside a function; that is a services module, allowed
                    bad.append((path.name, n))
    assert bad == []


def test_only_the_api_layer_and_its_router_use_the_pipeline_package():
    users = []
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND).as_posix()
        if rel.startswith(("tests/", "services/requirements_pipeline/", "venv")) or "site-packages" in rel:
            continue
        if "services.requirements_pipeline" in path.read_text(encoding="utf-8", errors="ignore") or "from services import requirements_pipeline" in path.read_text(encoding="utf-8", errors="ignore"):
            users.append(rel)
    assert sorted(users) == ["routers/job_requirements.py", "services/requirements_api.py"]


def test_v2_creation_extraction_and_scoring_stay_disabled():
    src = (BACKEND / "services" / "requirements_api.py").read_text(encoding="utf-8")
    assert "extract(" not in src and "parse_response" not in src
    for forbidden in ("ai_service", "criteria_worker", "deterministic_scoring", "cv_score", "llm_provider", "requirements_v2.extraction"):
        assert forbidden not in src
    assert not hasattr(pipe, "extract")


# ══ behaviour parity on all 48 stored responses ═══════════════════════════════════════════════════════════════════════════════════
def _replay():
    sys.path.insert(0, str(BACKEND / "scripts"))
    try:
        spec = importlib.util.spec_from_file_location("req_v2_pipeline_replay_svc", BACKEND / "scripts" / "requirements_v2_pipeline_replay.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(BACKEND / "scripts"))


def _same_view(prod: dict, cand: dict):
    for k in ("readiness", "gates", "unresolved_issues", "normalized_warnings", "informational"):
        assert prod[k] == cand[k], k


def test_all_48_stored_responses_evaluate_identically_through_the_stored_record():
    rp = _replay()
    cases = {c["id"]: c for c in rp.ev.load_cases()}
    seen = ok = 0
    for d in rp.RUNS.values():
        manifest = json.loads((d / "MANIFEST.json").read_text(encoding="utf-8"))
        prompt = {"version": manifest["prompt_version"], "sha256": manifest["prompt_sha256"]}
        for line in (d / "calls.jsonl").read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            seen += 1
            for policy in POLICIES:
                cand = extract(cases[rec["case"]]["jd"], rec["raw"], finish_reason=rec["finish_reason"], extraction_prompt=prompt,
                               require_classification_acknowledgment=policy)
                assert cand["ok"]
                record = pipe.to_record(cand, model="gpt-4o-mini-2024-07-18")
                assert json.loads(json.dumps(record)) == record and pipe.verify_record(record, cand["original"]) == []
                assert not {"requirements", "original", "readiness", "gates", "unresolved_issues"} & set(record)
                prod = pipe.evaluate(pipe.assemble_state(record, cand["requirements"], cand["original"]), require_classification_acknowledgment=policy)
                _same_view(prod, cand)
                ok += 1
    assert seen == 48 and ok == 96


# ══ behaviour parity on edit lifecycles (production plan_save versus the approved reconcile) ═══════════════════════════════════════════
def _client(doc: dict) -> dict:
    return api._public(doc)


def _step_prod(stored_doc, record, original, client, policy):
    plan = api.plan_save(stored_doc, client, reserved_ids=set(), retired_before=[], original=original, user_id="u", now=AT, record=record)
    new_record = plan.record if plan.record is not None else record
    ctx = api.pipeline_context(new_record, plan.doc, original, policy)
    assert ctx.status == "ok"
    return plan.doc, new_record, ctx.state, plan.audits


def _edit_steps(state):
    """Edits applied to a client document, in an order that exercises every gate."""
    def rust(d):
        for c in d["categories"].values():
            c["items"] = [i for i in c["items"] if i["text"] != "20 years of Rust experience"]
        return d

    def weights(d):
        for c, w in (("skills", 40), ("experience", 40), ("soft_skills", 20)):
            d["categories"][c]["weight"] = w
        return d

    def split(d):
        d["categories"]["skills"]["items"] = [i for i in d["categories"]["skills"]["items"] if i["text"] != "Java"]
        req = [i for i in d["categories"]["skills"]["items"] if i["importance"] == "required"]
        for n, x in enumerate(req):
            x["weight"] = 100 // len(req) + (1 if n < 100 % len(req) else 0)
        return d

    def restore_weights(d):
        for c, w in (("skills", 20), ("experience", 30), ("soft_skills", 50)):
            d["categories"][c]["weight"] = w
        return d
    return [rust, weights, split, restore_weights, weights]


@pytest.mark.parametrize("policy", POLICIES)
def test_edit_lifecycle_matches_the_approved_pipeline_step_by_step(policy):
    cand = combined()
    record = pipe.to_record(cand)
    original = cand["original"]
    prod_doc, cand_state = copy.deepcopy(cand["requirements"]), cand
    for step in _edit_steps(cand):
        client = step(_client(prod_doc))
        prod_doc, record, prod_state, audits = _step_prod(prod_doc, record, original, client, policy)
        # the approved reconcile fed with the SAME post-carry document
        new_doc_for_cand = copy.deepcopy(prod_doc)
        cand_state, _ = candidate_reconcile(cand_state, new_doc_for_cand, user_id="u", at=AT, require_classification_acknowledgment=policy)
        cand_state = candidate_evaluate(cand_state, require_classification_acknowledgment=policy)
        _same_view(prod_state, cand_state)
        assert record["review_records"] == cand_state["review_records"]


def test_production_never_stores_a_document_or_derived_values_in_the_record():
    cand = combined()
    record = pipe.to_record(cand)
    plan = api.plan_save(cand["requirements"], _client(cand["requirements"]), reserved_ids=set(), retired_before=[], original=cand["original"],
                         user_id="u", now=AT, record=record)
    assert plan.record is None and plan.changed is False                      # nothing changed, nothing rewritten
    assert set(record) == set(core.RECORD_KEYS)


# ══ record integrity ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
def test_verify_record_catches_damage_and_never_trusts_a_foreign_snapshot():
    cand = combined()
    good = pipe.to_record(cand)
    assert pipe.verify_record(good, cand["original"]) == []
    assert pipe.verify_record(None, cand["original"]) == ["record is not an object"]
    assert pipe.verify_record(good, None) == ["original snapshot missing"]
    other = copy.deepcopy(cand["original"])
    other["categories"]["skills"]["weight"] = 99
    assert "original snapshot does not match the pipeline record" in pipe.verify_record(good, other)
    for mutate, expected in [(lambda r: r["raw_response"].update(text="x"), "raw response hash mismatch"),
                             (lambda r: r.update(record_version="v0"), "unexpected record version"),
                             (lambda r: r.pop("review_records"), "missing key review_records"),
                             (lambda r: r["review_records"].pop("split_or"), "review_records must hold injection, split_or and model_warnings"),
                             (lambda r: r["review_records"]["injection"].update(jd_sha256="0" * 64), "injection record belongs to a different job description")]:
        bad = copy.deepcopy(good)
        mutate(bad)
        assert expected in pipe.verify_record(bad, cand["original"])


def test_to_record_refuses_a_failed_extraction():
    failed = extract(FIX["jd"], "not json", finish_reason="stop")
    with pytest.raises(pipe.PipelineError):
        pipe.to_record(failed)


# ══ view modes ════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════
def _view(cand, record, policy=True, editable=True):
    return api.build_view(job_id="j", revision=3, doc=cand["requirements"], original=cand["original"], policy=policy, editable=editable, pipeline_record=record)


def test_view_modes_guarded_unavailable_and_invalid():
    cand = combined()
    good = _view(cand, pipe.to_record(cand))
    assert good["pipeline"]["status"] == "ok" and good["readiness"]["guarded"] and good["readiness"]["basis"] == "pipeline"
    assert good["readiness"]["state"] == cand["readiness"]["state"] == "needs_injection_review"
    none = _view(cand, None)
    assert none["pipeline"]["status"] == "unavailable" and none["readiness"]["guarded"] is False and none["unresolved_issues"] is None
    assert none["original"] is not None and "provenance" not in none["pipeline"]
    broken = pipe.to_record(cand)
    broken["raw_response"]["text"] = "x"
    inv = _view(cand, broken)
    assert inv["pipeline"]["status"] == "invalid_record" and inv["readiness"]["state"] == "pipeline_record_invalid" and inv["readiness"]["can_proceed"] is False
    assert inv["unresolved_issues"] is None and inv["requirements"] and inv["original"]
    viewer = _view(cand, pipe.to_record(cand), editable=False)
    assert "text" not in viewer["pipeline"]["raw_response"] and "raw_ai_output" not in viewer["pipeline"]


def test_the_view_is_json_serializable_and_derived_values_come_from_the_document():
    cand = combined()
    record = pipe.to_record(cand)
    v = _view(cand, record)
    json.dumps(v)
    doc = copy.deepcopy(cand["requirements"])
    doc = remove_item(doc, next(i["id"] for c in doc["categories"].values() for i in c["items"] if i["text"] == "20 years of Rust experience"))
    after = api.build_view(job_id="j", revision=3, doc=doc, original=cand["original"], policy=True, editable=True, pipeline_record=record)
    assert any(i["kind"] == "injection_requirement" for i in v["unresolved_issues"]) and not any(i["kind"] == "injection_requirement" for i in after["unresolved_issues"])


def test_invalid_documents_are_refused_before_any_record_is_touched():
    cand = combined()
    record = pipe.to_record(cand)
    bad = _client(cand["requirements"])
    bad["categories"]["skills"]["weight"] = 1
    with pytest.raises(api.ApiError) as e:
        api.plan_save(cand["requirements"], bad, reserved_ids=set(), retired_before=[], original=cand["original"], record=record)
    assert e.value.http_status == 422 and e.value.code == "invalid_requirements"


def test_client_supplied_server_state_inside_the_document_is_discarded_and_listed():
    cand = combined()
    client = _client(cand["requirements"])
    client.update(review_records={"injection": {"issues": []}}, raw_response={"text": "x"}, readiness={"can_proceed": True}, requirements_pipeline={"a": 1})
    plan = api.plan_save(cand["requirements"], client, reserved_ids=set(), retired_before=[], original=cand["original"], record=pipe.to_record(cand),
                         body_discarded=["gates"])
    assert {"review_records", "raw_response", "readiness", "requirements_pipeline", "body.gates"} <= set(plan.discarded)
    assert "readiness" not in plan.doc and "review_records" not in plan.doc


def test_acknowledging_a_gate_without_an_acknowledgment_is_refused_by_the_service():
    cand = combined()
    for gate in ("injection", "split_or", "structure", "confirmation"):
        with pytest.raises(pipe.PipelineError) as e:
            pipe.acknowledge_gate(pipe.to_record(cand)["review_records"], cand["requirements"], gate, "x", user_id="u", at=AT)
        assert e.value.code == "not_acknowledgeable"
    with pytest.raises(pipe.PipelineError) as e:
        pipe.acknowledge_gate(pipe.to_record(cand)["review_records"], cand["requirements"], "mystery", "x", user_id="u", at=AT)
    assert e.value.code == "unknown_gate"
