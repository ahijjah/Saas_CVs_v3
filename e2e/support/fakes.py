"""Test-only replacements for the external services of the backend under test.

Only the model call (scripted from a marker in the job description), the sentence-embedding model (a deterministic encoder with the
same interface; the real weights are not available here), the AI scoring call and outbound e-mail are replaced. Nothing in this
module is imported by the production code. It is loaded only by backend_app.py, the entry point the E2E stack starts."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading

import numpy as np

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
    """The model output for a job description. A scenario marker selects the output; Arabic wording gets the Arabic document."""
    if "SCENARIO-RETRY-BROKEN" in description:
        return "{not json"                      # fails on every attempt until the description is corrected to SCENARIO-RETRY-FIXED
    if "SCENARIO-RETRY-FIXED" in description:
        return _response({"skills": [_item("Python", "required", "Python and SQL are required for every candidate")],
                          "experience": [_item("Backend experience", "preferred", "Five years of backend experience is preferred",
                                               cue="preferred")]}, {"skills": 100})
    if "SCENARIO-INVALID" in description:
        return "{not json"
    if "SCENARIO-GATES" in description:
        return _response({"skills": [_item("Valid driving licence", "required", "must hold a valid driving licence"),
                                     _item("Python", "preferred", "Python is a plus", cue="a plus")]}, {"skills": 100})
    if "SCENARIO-ITEMS" in description:
        return _response({"skills": [_item("Python", "required", "Python is required"),
                                     _item("Arabic fluency", "required", "Fluent Arabic is required")],
                          "experience": [_item("Backend experience", "preferred", "Backend experience is preferred", cue="preferred")]},
                         {"skills": 100})
    if "SCENARIO-MATRIX" in description:
        return _response({"skills": [_item("SQL troubleshooting", "required", "SQL troubleshooting is required"),
                                     _item("Python", "required", "Python is required"),
                                     _item("Incident management", "required", "Incident management is required"),
                                     _item("English fluency", "required", "English fluency is required"),
                                     _item("Arabic fluency", "required", "Arabic fluency is required"),
                                     _item("Kubernetes", "preferred", "Kubernetes is preferred", cue="preferred")]}, {"skills": 100})
    if "SCENARIO-PREFERRED" in description:
        return _response({"experience": [_item("Reporting tools", "preferred", "Knowledge of reporting tools is preferred",
                                               cue="preferred")]}, {})
    if "SCENARIO-CLASSIFICATION" in description:
        return _response({"skills": [_item("Python", "required", "Python is required")],
                          "experience": [_item("Backend experience", "preferred", "Five years of backend experience is valued",
                                               cue="nice to have")]}, {"skills": 100})   # "valued" is not optional wording: a real warning
    if "SCENARIO-SPLIT" in description:
        return _response({"skills": [_item("Python", "required", "Python or Java is required", alternatives=["Python", "Java"]),
                                     _item("Java", "required", "Python or Java is required", alternatives=["Python", "Java"])],
                          "experience": [_item("Experience", "preferred", "Five years of experience is preferred", cue="preferred")]},
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


def scripted_item_evaluation(user_message: str) -> str:
    """The item-level evaluation of the E2E CV (backend/tests/fixtures/requirements_v2_cv_smoke.pdf). The results depend on the requirement's wording:
    Python is met, SQL partially met, anything about Arabic or a licence is unclear (the CV does not say), a Preferred backend item is a yes.
    Every quotation is copied from the CV text, as the real evaluation must do."""
    requirements = json.loads(re.search(r"Requirements:\n(.*?)\n\nCV Text:", user_message, re.S).group(1))
    cv_text = user_message.split("CV Text:\n", 1)[1] if "CV Text:\n" in user_message else ""
    # A CV may state a level per requirement as "<Requirement>: advanced|basic|none|yes." and is then evaluated by that sentence, quoted exactly.
    stated = {m.group(1).strip().lower(): (m.group(0), m.group(2)) for m in re.finditer(r"([A-Za-z][A-Za-z /]{2,40}): (advanced|basic|none|yes)\.", cv_text)}
    out = []
    for r in requirements:
        text = r["requirement"].lower()
        if text in stated:
            sentence, level = stated[text]
            result = {"advanced": "met", "basic": "partially_met", "none": "not_met", "yes": "yes"}[level]
            if r["importance"] == "preferred" and result != "yes":
                result = "no"
            out.append({"item_id": r["item_id"], "result": result, "evidence_quotes": [sentence],
                        "explanation": f"The CV states {level}.", "confidence": 0.9})
            continue
        if r["importance"] == "preferred":
            if "backend" in text:
                out.append({"item_id": r["item_id"], "result": "yes", "evidence_quotes": ["five years of backend experience"],
                            "explanation": "The CV states five years of backend experience.", "confidence": 0.9})
            else:
                out.append({"item_id": r["item_id"], "result": "not_clear", "evidence_quotes": [], "explanation": "Not stated.", "confidence": 0.7})
        elif "python" in text or "بايثون" in text:
            out.append({"item_id": r["item_id"], "result": "met", "evidence_quotes": ["Strong Python and SQL skills"],
                        "explanation": "The CV shows strong Python skills.", "confidence": 0.95})
        elif "sql" in text:
            out.append({"item_id": r["item_id"], "result": "partially_met", "evidence_quotes": ["query tuning, migrations and data modelling"],
                        "explanation": "SQL skills are shown, but not at the stated depth.", "confidence": 0.8})
        else:
            out.append({"item_id": r["item_id"], "result": "unclear", "evidence_quotes": [], "explanation": "The CV does not say.", "confidence": 0.5})
    named = re.match(r"\s*([A-Za-z][A-Za-z ]{2,40}) - candidate summary", cv_text)
    return json.dumps({"candidate_name": named.group(1) if named else "Jane Candidate", "candidate_email": "", "candidate_phone": "", "evaluations": out,
                       "evaluation_notes": "E2E evaluation.", "strengths": ["Python"], "gaps_identified": [], "red_flags": [],
                       "interview_questions": []}, ensure_ascii=False)


class _Completions:
    async def create(self, **request):
        system = request["messages"][0]["content"] if request["messages"] else ""
        if isinstance(system, str) and system.startswith("You are an expert HR analyst. You check ONE candidate CV"):
            return _Response(scripted_item_evaluation(request["messages"][-1]["content"]))
        description = request["messages"][-1]["content"]
        match = re.search(r"<<<JD\n(.*?)\nJD>>>", description, re.S)
        return _Response(scripted_response(match.group(1) if match else description))


class FakeAIClient:
    class _Chat:
        completions = _Completions()

    chat = _Chat()


class DeterministicEncoder:
    """Stands in for the sentence-embedding model (interface: encode(sentences, normalize_embeddings=...))."""

    def encode(self, sentences, normalize_embeddings=True):
        rows = []
        for sentence in sentences:
            vector = np.zeros(512)
            for token in re.findall(r"\w+", sentence.lower()):
                vector[int(hashlib.sha256(token.encode()).hexdigest()[:8], 16) % 512] += 1
            norm = np.linalg.norm(vector)
            rows.append(vector / norm if norm else vector)
        return np.array(rows)


async def fake_score_cv(cv_text, criteria, job_title, **kwargs):
    return ({"score_skills": 80, "score_experience": 70, "score_education": 60, "score_certifications": 50,
             "score_soft_skills": 50, "score_domain_knowledge": 50, "score_other": 50, "strengths": ["fixture"],
             "gaps_identified": [], "red_flags": [], "evaluation_notes": "e2e", "interview_questions": [],
             "reasoning": {}, "score_details": {}},
            {"model": "fake-scoring-model", "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "finish_reason": "stop"})


async def no_email(*args, **kwargs):
    return None


async def fake_registry(db, stage):
    from services.ai_model_registry_service import ResolvedModel
    return ResolvedModel(provider="fake", model_name="fake-extraction-model", client=FakeAIClient(), model_id=None)


def patch_external(files_root: str) -> None:
    """Install the test doubles. Must run before the application is imported by uvicorn."""
    os.environ["FILES_BASE_PATH"] = files_root
    import services.ai_model_registry_service as registry
    import services.ai_service as ai_service
    import services.email_service as email_service
    import services.local_processor as local_processor
    registry.resolve_stage_client = fake_registry
    ai_service.score_cv = fake_score_cv
    email_service.send_cv_received_email = no_email
    # The API lifespan calls ensure_model_loaded(), which returns at once when _MODEL is set, so the real model is never loaded.
    local_processor._MODEL = DeterministicEncoder()
    import workers.criteria_worker as legacy
    import workers.cv_score as cv_score
    import workers.requirements_v2_worker as v2

    def queue_in_thread(task):
        def delay(*args, **kwargs):
            threading.Thread(target=lambda: task.apply(args=args, kwargs=kwargs), daemon=True).start()
        return delay

    real_parse = v2.parse_response

    def parse_keeping_the_split(content, description, finish_reason=None, **kwargs):
        if "SCENARIO-SPLIT" in description:
            kwargs["merge_alternatives"] = False    # this journey reviews a split that reached storage; the parser would merge it
        return real_parse(content, description, finish_reason, **kwargs)

    v2.parse_response = parse_keeping_the_split
    v2.extract_requirements_v2_task.delay = queue_in_thread(v2.extract_requirements_v2_task)
    legacy.extract_criteria_task.delay = queue_in_thread(legacy.extract_criteria_task)
    cv_score.score_cv_task.delay = queue_in_thread(cv_score.score_cv_task)
    cv_score.rescore_application_task.delay = queue_in_thread(cv_score.rescore_application_task)
