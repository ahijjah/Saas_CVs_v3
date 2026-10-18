"""Run the REAL API on a disposable PostgreSQL 16 built from the full migration chain, for the browser acceptance.

Only external components are replaced: the model call (scripted by a scenario marker in the job description), the sentence-embedding
model (its weights are not available in this environment; a deterministic encoder with the same interface stands in), the AI scoring
response, and outbound e-mail. Tasks are queued through a thread instead of a broker, so the same worker code runs.

    python -m tests.acceptance.live_backend --state /path/to/state.json     (run from the backend directory)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import uuid
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[2]
for _path in (BACKEND, BACKEND / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from realdb_helper import built_chain  # noqa: E402  (builds the migration chain on a disposable cluster)

PORT = 8000
ORIGIN = "http://localhost:3000"
EN_READY = "Senior Python developer.\nPython and SQL are required for every candidate.\nFive years of backend experience is preferred."
AR_READY = "مطور أول لبناء واجهات برمجية.\nمطلوب بايثون وإتقان لغة SQL لكل مرشح.\nخبرة سابقة في الذكاء الاصطناعي مفضلة."


def _item(text, importance, source, *, cue=None, alternatives=None, experience=None):
    item = {"text": text, "importance": importance, "source_text": source, "origin": "stated"}
    if cue is not None:
        item["importance_cue"] = cue
    if alternatives is not None:
        item["alternatives"] = alternatives
    if experience is not None:
        item["experience"] = experience
    return item


def _response(categories, weights):
    cats = {c: [] for c in ("skills", "experience", "education", "certifications", "soft_skills", "domain_knowledge",
                            "other_requirements")}
    cats.update(categories)
    return json.dumps({"categories": cats, "category_weights": weights, "scoreability": {"status": "scoreable"}}, ensure_ascii=False)


def scripted_response(description: str) -> str:
    """The model output for a description: a scenario marker selects it, Arabic wording gets the Arabic document, English the default."""
    if "SCENARIO-INVALID" in description:
        return "{not json"
    if "SCENARIO-PREFERRED" in description:
        return _response({"experience": [_item("Reporting tools", "preferred", "Knowledge of reporting tools is preferred",
                                               cue="preferred")]}, {})
    if "SCENARIO-CLASSIFICATION" in description:
        return _response({"skills": [_item("Python", "required", "Python is required")],
                          "experience": [_item("Backend experience", "preferred", "Five years of backend experience is preferred",
                                               cue="nice to have")]}, {"skills": 100})
    if "SCENARIO-SPLIT" in description:
        return _response({"skills": [_item("Python", "required", "Python or Java is required", alternatives=["Python", "Java"]),
                                     _item("Java", "required", "Python or Java is required", alternatives=["Python", "Java"])]},
                         {"skills": 100})
    if "SCENARIO-STRUCTURE" in description:
        return _response({"skills": [_item("Python", "required", "Python is required")],
                          "experience": [_item("Backend experience", "required", "Five years of backend experience is required",
                                               experience={"subject": "backend experience", "min_years": 5})]},
                         {"skills": 60, "experience": 40})
    if re.search(r"[؀-ۿ]", description):
        return _response({"skills": [_item("بايثون", "required", "مطلوب بايثون وإتقان لغة SQL"),
                                     _item("SQL", "required", "إتقان لغة SQL")],
                          "experience": [_item("خبرة في الذكاء الاصطناعي", "preferred", "خبرة سابقة في الذكاء الاصطناعي مفضلة",
                                               cue="مفضلة")]}, {"skills": 100})
    return _response({"skills": [_item("Python", "required", "Python and SQL are required for every candidate"),
                                 _item("SQL", "required", "Python and SQL are required for every candidate")],
                      "experience": [_item("Backend experience", "preferred", "Five years of backend experience is preferred",
                                           cue="preferred")]}, {"skills": 100})


class _Message:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Message(content)
        self.finish_reason = "stop"


class _Response:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class _Completions:
    async def create(self, **request):
        description = request["messages"][-1]["content"]
        match = re.search(r"<<<JD\n(.*?)\nJD>>>", description, re.S)
        return _Response(scripted_response(match.group(1) if match else description))


class FakeAIClient:
    class _Chat:
        completions = _Completions()

    chat = _Chat()


class DeterministicEncoder:
    """Stands in for the sentence-embedding model only (interface: encode(sentences, normalize_embeddings=...))."""

    def encode(self, sentences, normalize_embeddings=True):
        rows = []
        for sentence in sentences:
            vector = np.zeros(512)
            for token in re.findall(r"\w+", sentence.lower()):
                vector[int(hashlib.sha256(token.encode()).hexdigest()[:8], 16) % 512] += 1
            norm = np.linalg.norm(vector)
            rows.append(vector / norm if norm else vector)
        return np.array(rows)


async def _fake_score_cv(cv_text, criteria, job_title, **kwargs):
    return ({"score_skills": 80, "score_experience": 70, "score_education": 60, "score_certifications": 50,
             "score_soft_skills": 50, "score_domain_knowledge": 50, "score_other": 50, "strengths": ["fixture"],
             "gaps_identified": [], "red_flags": [], "evaluation_notes": "acceptance", "interview_questions": [],
             "reasoning": {}, "score_details": {}},
            {"model": "fake-scoring-model", "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "finish_reason": "stop"})


async def _no_email(*args, **kwargs):
    return None


async def _fake_registry(db, stage):
    from services.ai_model_registry_service import ResolvedModel
    return ResolvedModel(provider="fake", model_name="fake-extraction-model", client=FakeAIClient(), model_id=None)


def _patch_external(files_root: str):
    os.environ["FILES_BASE_PATH"] = files_root
    import services.ai_model_registry_service as registry
    import services.ai_service as ai_service
    import services.email_service as email_service
    import services.local_processor as local_processor
    registry.resolve_stage_client = _fake_registry
    ai_service.score_cv = _fake_score_cv
    email_service.send_cv_received_email = _no_email
    local_processor._get_model = lambda: DeterministicEncoder()
    import workers.criteria_worker as legacy
    import workers.cv_score as cv_score
    import workers.requirements_v2_worker as v2

    def queue_in_thread(task):
        def delay(*args, **kwargs):
            threading.Thread(target=lambda: task.apply(args=args, kwargs=kwargs), daemon=True).start()
        return delay

    v2.extract_requirements_v2_task.delay = queue_in_thread(v2.extract_requirements_v2_task)
    legacy.extract_criteria_task.delay = queue_in_thread(legacy.extract_criteria_task)
    cv_score.score_cv_task.delay = queue_in_thread(cv_score.score_cv_task)


def _seed(db, state: dict):
    from auth.password import hash_password
    cur = db.conn.cursor()
    tag = uuid.uuid4().hex[:6]
    cur.execute("INSERT INTO tenants (name, email_domain) VALUES (%s, %s) RETURNING tenant_id", (f"Acceptance {tag}", f"{tag}.example"))
    tenant = str(cur.fetchone()[0])
    users = {}
    for role in ("admin", "recruiter", "viewer"):
        email = f"{role}-{tag}@acceptance-{tag}.com"
        password = f"Accept-{role}-2026"
        cur.execute("INSERT INTO users (tenant_id, email, password_hash, full_name, role, status, must_change_password)"
                    " VALUES (%s, %s, %s, %s, %s, 'active', false) RETURNING user_id",
                    (tenant, email, hash_password(password), role.title(), role))
        users[role] = {"email": email, "password": password, "user_id": str(cur.fetchone()[0])}
    state.update({"tenant": tenant, "users": users, "port": PORT, "origin": ORIGIN})
    state["admin_user_id"] = users["admin"]["user_id"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True)
    args = parser.parse_args()
    state: dict = {}
    files_root = str(Path(args.state).resolve().parent / "files")
    Path(files_root).mkdir(parents=True, exist_ok=True)
    with built_chain() as db:
        dsn = f"postgresql+asyncpg://postgres@127.0.0.1:{db.cluster.port}/{db.name}"
        os.environ.update({
            "DATABASE_URL": dsn, "DB_SCHEMA": "cv_analyzer", "JWT_SECRET": "acceptance-only-secret-0123456789abcdef",
            "CORS_ORIGINS": json.dumps([ORIGIN]), "APP_BASE_URL": ORIGIN, "API_BASE_URL": f"http://127.0.0.1:{PORT}",
        })
        _seed(db, state)
        _patch_external(files_root)
        state.update({"dsn": dsn, "files_root": files_root})
        Path(args.state).write_text(json.dumps(state, indent=2), encoding="utf-8")
        import uvicorn
        from main import app
        uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
