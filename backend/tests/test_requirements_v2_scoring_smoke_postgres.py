"""Smoke: a requirements-v2 score through the REAL pipeline, with a REAL PDF and the REAL local gatekeeper, on real PostgreSQL.

Real: the PDF bytes on disk, PyMuPDF text extraction, the quality gate, the gatekeeper's decision logic and skill matching, the
criteria, the revision lock, and the score row.

Replaced (external to this repository's logic): the AI scoring response, the outbound e-mail, and the sentence-embedding model's
weights. The weights are not available in this environment (the model host is unreachable), so the semantic step uses a deterministic
bag-of-words encoder with the same interface. The gatekeeper's thresholds, its decisions and its skill matching are unchanged."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import config
from runtime_engines import async_engine, async_url
from realdb_helper import EXPECTED_PRE_EXISTING_FAILURES, built_chain, check_chain, unavailable_reason
from services import ai_service
from services import local_processor
from services.requirements_v2 import contract as C
from services.requirements_v2.extraction import parse_response
from workers import cv_score
from workers import requirements_v2_worker as v2_worker

FIXTURE = Path(__file__).parent / "fixtures" / "requirements_v2_cv_smoke.pdf"
JD = ("Senior Python developer.\nPython and SQL are required for every candidate.\n"
      "Five years of backend experience is preferred.")
NOW = "2026-10-18T10:00:00+00:00"


class _DeterministicEncoder:
    """Stands in for the sentence-embedding model only: normalised bag-of-words vectors, so similar texts score higher."""

    def encode(self, sentences, normalize_embeddings=True):
        rows = []
        for sentence in sentences:
            vector = np.zeros(512)
            for token in re.findall(r"\w+", sentence.lower()):
                vector[int(hashlib.sha256(token.encode()).hexdigest()[:8], 16) % 512] += 1
            norm = np.linalg.norm(vector)
            rows.append(vector / norm if norm else vector)
        return np.array(rows)


def _document():
    cats = {c: [] for c in C.CATEGORIES}
    cats["skills"] = [
        {"text": "Python", "importance": "required", "source_text": "Python and SQL are required for every candidate", "origin": "stated"},
        {"text": "SQL", "importance": "required", "source_text": "Python and SQL are required for every candidate", "origin": "stated"}]
    raw = {"categories": cats, "category_weights": {"skills": 100}, "scoreability": {"status": "scoreable"}}
    parsed = parse_response(json.dumps(raw), JD, "stop", id_factory=v2_worker.stable_id_factory(JD))
    doc, expectations = v2_worker.finalise_document(v2_worker.checked_document(parsed), JD)
    return doc, parsed.original, expectations


@pytest.fixture(scope="module")
def built():
    reason = unavailable_reason()
    if reason:
        pytest.skip(reason)
    with built_chain() as db:
        check_chain(db.failures)
        assert {f.file for f in db.failures} == EXPECTED_PRE_EXISTING_FAILURES
        yield db


@pytest.mark.asyncio
async def test_a_real_pdf_scores_through_the_real_pipeline_and_records_the_revision_used(built, monkeypatch, tmp_path):
    dsn = async_url(built, "cv_worker")
    real = config.get_settings()
    monkeypatch.setattr(config, "get_settings", lambda: real.model_copy(update={
        "database_url": dsn, "db_schema": "cv_analyzer", "files_base_path": str(tmp_path)}))
    import services.ai_model_registry_service as registry
    async def no_registry(db, stage):
        return None
    monkeypatch.setattr(registry, "resolve_stage_client", no_registry)
    monkeypatch.setattr(local_processor, "_get_model", lambda: _DeterministicEncoder())
    import services.email_service as email_service
    async def no_email(*args, **kwargs):
        return None
    monkeypatch.setattr(email_service, "send_cv_received_email", no_email)
    scored = []

    class _ItemClient:
        """The model of the item-level evaluation: every requirement is met (a Preferred one: yes), quoting the real PDF's text."""

        def __init__(self):
            self.chat = type("Chat", (), {"completions": self})()

        async def create(self, **kwargs):
            user = kwargs["messages"][1]["content"]
            requirements = json.loads(user.split("Requirements:\n", 1)[1].split("\n\nCV Text:", 1)[0])
            scored.append({"cv_text": user.split("\n\nCV Text:\n", 1)[1], "criteria": {
                c: [r["requirement"] for r in requirements if r["category"] == c and r["importance"] == "required"] for c in C.CATEGORIES}})
            evaluations = [{"item_id": r["item_id"], "result": "met" if r["importance"] == "required" else "yes",
                            "evidence_quotes": ["Strong Python and SQL skills"], "explanation": "stated", "confidence": 0.95} for r in requirements]
            content = json.dumps({"candidate_name": "Jane", "evaluations": evaluations, "evaluation_notes": "smoke"})
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
                                   usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2))

    monkeypatch.setattr(ai_service, "_get_client", lambda: _ItemClient())          # a v2 job is scored per item through this client

    tenant_tag = uuid.uuid4().hex[:8]
    cur = built.conn.cursor()
    cur.execute("INSERT INTO tenants (name, email_domain) VALUES (%s, %s) RETURNING tenant_id", (f"t {tenant_tag}", f"{tenant_tag}.example"))
    tenant = str(cur.fetchone()[0])
    cur.execute("INSERT INTO users (tenant_id, email, password_hash, full_name, role) VALUES (%s, %s, 'x', 'u', 'admin') RETURNING user_id",
                (tenant, f"{tenant_tag}@example.test"))
    user = str(cur.fetchone()[0])
    cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description, status) VALUES (%s, %s, 'Senior', %s, 'active') RETURNING job_id",
                (tenant, user, JD))
    job = str(cur.fetchone()[0])
    document, original, expectations = _document()
    cur.execute("INSERT INTO job_criteria (job_id, criteria_extraction_status, requirements_schema_version, requirements_revision,"
                " analysis_json, original_analysis_json, weight_skills, weight_experience, weight_education, weight_certifications,"
                " weight_soft_skills, weight_domain_knowledge, weight_other)"
                " VALUES (%s, 'completed', 2, 4, CAST(%s AS jsonb), CAST(%s AS jsonb), %s, %s, %s, %s, %s, %s, %s)",
                (job, json.dumps({"requirements": document}, ensure_ascii=False),
                 json.dumps({"requirements": original, "structural_expectations": expectations}, ensure_ascii=False),
                 *[document["categories"][c]["weight"] for c in C.CATEGORIES]))

    cur.execute("INSERT INTO applications (job_id, tenant_id, candidate_name, candidate_email, candidate_phone, submission_source,"
                " processing_status) VALUES (%s, %s, 'Jane', %s, '+100', 'manual_upload', 'pending') RETURNING application_id",
                (job, tenant, f"{tenant_tag}-c@example.test"))
    app = str(cur.fetchone()[0])
    relative = f"tenants/{tenant}/jobs/{job}/{app}.pdf"
    (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / relative).write_bytes(FIXTURE.read_bytes())          # the real PDF bytes, on disk, where the pipeline reads them
    cur.execute("INSERT INTO application_files (application_id, tenant_id, original_name, mime_type, file_path, file_size_bytes,"
                " extracted_text, extraction_status) VALUES (%s, %s, 'cv.pdf', 'application/pdf', %s, %s, '', 'pending')",
                (app, tenant, relative, FIXTURE.stat().st_size))

    engine = async_engine(built, "cv_worker")
    try:
        await cv_score._score_cv_async(app, job, tenant, str(tmp_path / relative), "application/pdf", {},
                                       async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False))
    finally:
        await engine.dispose()

    cur.execute("SELECT final_score, requirements_revision, status FROM application_scoring_runs WHERE application_id = %s AND is_current", (app,))
    rows = cur.fetchall()
    assert len(rows) == 1, rows
    final_score, revision_used, status = rows[0]
    assert revision_used == 4 and status == "scored" and final_score == 100
    assert "Python" in scored[0]["cv_text"] and len(scored[0]["cv_text"]) >= 300      # the text really came from the PDF
    assert scored[0]["criteria"]["skills"] == ["Python", "SQL"]
