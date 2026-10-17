"""Requirements-v2 extraction FOUNDATIONS (offline). The ported contract, prompt identity and parser entry points, checked on the
B02 and B12 fixtures. No model call, no network, no database, and nothing here changes production behaviour: no production
module imports the package, and the pinned prompt is loaded and verified but not activated."""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

from services.requirements_v2 import CATEGORIES
from services.requirements_v2.extraction import parse_response
from services.requirements_v2.extraction.prompt import PROMPT_CODE, PROMPT_SHA256, PROMPT_VERSION, load_prompt

BACKEND = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = BACKEND / "tests" / "fixtures" / "requirements_v2_foundations"
PROMPT_FILE = BACKEND / "services" / "requirements_v2" / "extraction" / "prompts" / "criteria_extraction_v2-1.txt"
PRODUCTION_PATHS = ["routers", "workers", "auth", "main.py", "database.py", "config.py"]
# The one service module approved to import the package. It is reachable from no route, worker, auth, startup or config path,
# which PRODUCTION_PATHS checks for every module.
APPROVED_SERVICE_IMPORTERS = {"services/requirements_edit.py"}
# The workers approved to import the package: the requirements-v2 extraction worker, and the scoring worker's requirements-v2 branch
# (the adapter and revision guard in services/requirements_v2/scoring.py). No other production module may import it.
APPROVED_WORKER_IMPORTERS = {"workers/requirements_v2_worker.py", "workers/cv_score.py"}


def _case(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


B02 = _case("B02_en_data_analyst")
B12 = _case("B12_ar_injection")


def test_pinned_prompt_identity_is_unchanged():
    assert PROMPT_CODE == "criteria_extraction_v2"
    assert PROMPT_VERSION == "criteria_extraction_v2-1"
    assert PROMPT_SHA256 == "f2017d2879554935baa5d0d4f51d53fa0600e18858cc875d4a791c5ac0678b04"
    assert PROMPT_FILE.read_bytes().decode("utf-8") == load_prompt()


def test_the_pinned_prompt_file_matches_its_recorded_digest():
    import hashlib
    assert hashlib.sha256(PROMPT_FILE.read_bytes()).hexdigest() == PROMPT_SHA256


def test_b02_and_b12_fixture_expectations_are_literal_jd_substrings():
    for case in (B02, B12):
        jd = case["jd"]
        assert case["expected"]["items"], case["id"]
        for item in case["expected"]["items"]:
            assert item["evidence"] in jd, (case["id"], item["evidence"])
            assert item["category"] in CATEGORIES, (case["id"], item["category"])
            if item["cue"]:
                assert item["cue"] in jd, (case["id"], item["cue"])


def test_a_minimal_valid_response_for_b02_is_accepted_as_a_draft():
    response = {"categories": {"skills": [{"text": "Python or R proficiency", "importance": "required",
                                           "source_text": "Proficiency in Python or R", "alternatives": ["Python", "R"]}]}}
    result = parse_response(json.dumps(response), B02["jd"])
    assert result.status == "draft"
    assert result.errors == ()
    assert result.prompt_version == PROMPT_VERSION and result.prompt_sha256 == PROMPT_SHA256


def test_failures_fail_closed_with_their_codes():
    jd = B02["jd"]
    assert parse_response("", jd).status == "failed"
    assert parse_response('{"categories": {}}', jd, finish_reason="length").errors[0].code == "output_truncated"
    assert parse_response("not json", jd).errors[0].code == "invalid_json"
    assert parse_response("[]", jd).errors[0].code == "not_an_object"
    assert parse_response('{"no": "categories"}', jd).errors[0].code == "missing_categories"


def test_the_package_imports_without_database_model_or_configuration():
    probe = ("import sys, services.requirements_v2.extraction as E\n"
             "bad = [m for m in ('database', 'sqlalchemy', 'asyncpg', 'openai', 'config', 'auth', 'routers', 'workers') if m in sys.modules]\n"
             "print(sorted(bad))\n")
    out = subprocess.run([sys.executable, "-c", probe], cwd=BACKEND, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]", out.stdout


def test_no_production_module_imports_the_new_package():
    pattern = re.compile(r"services\.requirements_v2|from services import requirements_v2")
    offenders = []
    for rel in PRODUCTION_PATHS:
        p = BACKEND / rel
        files = [p] if p.is_file() else list(p.rglob("*.py")) if p.exists() else []
        for f in files:
            if f.relative_to(BACKEND).as_posix() in APPROVED_WORKER_IMPORTERS:
                continue
            if pattern.search(f.read_text(encoding="utf-8", errors="ignore")):
                offenders.append(str(f.relative_to(BACKEND)))
    for f in (BACKEND / "services").rglob("*.py"):
        if "requirements_v2" in f.relative_to(BACKEND / "services").parts:
            continue                                   # the package itself
        if f.relative_to(BACKEND).as_posix() in APPROVED_SERVICE_IMPORTERS:
            continue                                   # the editing service: a service module with no route, worker or config
        if pattern.search(f.read_text(encoding="utf-8", errors="ignore")):
            offenders.append(str(f.relative_to(BACKEND)))
    assert offenders == [], offenders
