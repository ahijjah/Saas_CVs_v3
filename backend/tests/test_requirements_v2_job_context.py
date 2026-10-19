"""Job context (location, work arrangement, reports_to): optional, read-only, never scored. English and Arabic."""
from __future__ import annotations

import copy
import hashlib
import json
import pathlib

import pytest
from pydantic import ValidationError

from routers.job_requirements import ContextOut, DocumentOut
from services.requirements_edit import _project_document
from services.requirements_v2.contract import CATEGORIES, CONTEXT_FIELDS
from services.requirements_v2.extraction.parser import parse_response
from services.requirements_v2.extraction.prompt import PROMPT_SHA256, PROMPT_VERSION, load_prompt
from services.requirements_v2.readiness import carry_context, carry_server_owned, compute_readiness
from services.requirements_v2.validation import validate_draft, validate_final

EN_JD = (
    "IT Support Engineer\n"
    "Requirements:\n"
    "- The candidate must know Python or PowerShell scripting.\n"
    "The position is hybrid in Ramallah and reports to the IT Operations Manager.\n"
)
EN_SENTENCE = "The position is hybrid in Ramallah and reports to the IT Operations Manager."
AR_SENTENCE = "الوظيفة هجينة في رام الله وتتبع مباشرة لمدير عمليات تقنية المعلومات."
AR_JD = "مهندس دعم فني\nالمتطلبات:\n- إجادة لغة البرمجة Python.\n" + AR_SENTENCE + "\n"


def _ai(context, jd_skill_text, skill_source, *, extra_context=None):
    ai = {"categories": {c: [] for c in CATEGORIES}, "category_weights": {"skills": 100}}
    ai["categories"]["skills"] = [{"text": jd_skill_text, "importance": "required", "importance_cue": None,
                                   "source_text": skill_source, "origin": "stated", "alternatives": None, "experience": None}]
    if context is not None:
        ai["context"] = context
    return ai


def _parse(ai, jd):
    res = parse_response(json.dumps(ai), jd)
    assert res.status == "draft", res.errors
    return res


EN_CONTEXT = {"location": "Ramallah", "work_arrangement": "hybrid", "reports_to": "IT Operations Manager", "source_text": EN_SENTENCE}
AR_CONTEXT = {"location": "رام الله", "work_arrangement": "hybrid", "reports_to": "مدير عمليات تقنية المعلومات", "source_text": AR_SENTENCE}


# ── the acceptance case ─────────────────────────────────────────────────────────────────────────────────────────────

def test_acceptance_sentence_gives_location_arrangement_and_reports_to():
    res = _parse(_ai(EN_CONTEXT, "Python or PowerShell scripting", "The candidate must know Python or PowerShell scripting."), EN_JD)
    assert res.requirements["context"] == {"location": "Ramallah", "work_arrangement": "hybrid",
                                           "reports_to": "IT Operations Manager"}


def test_context_values_never_become_scored_requirements():
    with_context = _parse(_ai(EN_CONTEXT, "Python or PowerShell scripting", "The candidate must know Python or PowerShell scripting."), EN_JD)
    without = _parse(_ai(None, "Python or PowerShell scripting", "The candidate must know Python or PowerShell scripting."), EN_JD)
    def shape(doc):
        return {c: (doc["categories"][c]["weight"], [(i["text"], i["importance"], i["weight"], i["alternatives"])
                                                       for i in doc["categories"][c]["items"]]) for c in CATEGORIES}
    assert shape(with_context.requirements) == shape(without.requirements)
    texts = [i["text"] for c in CATEGORIES for i in with_context.requirements["categories"][c]["items"]]
    assert not any(t in ("Ramallah", "Hybrid", "IT Operations Manager") for t in texts)


def test_context_has_no_effect_on_readiness_or_weights():
    with_context = _parse(_ai(EN_CONTEXT, "Python or PowerShell scripting", "The candidate must know Python or PowerShell scripting."), EN_JD)
    without = _parse(_ai(None, "Python or PowerShell scripting", "The candidate must know Python or PowerShell scripting."), EN_JD)
    assert compute_readiness(with_context.requirements) == compute_readiness(without.requirements)
    for c in CATEGORIES:
        assert with_context.requirements["categories"][c]["weight"] == without.requirements["categories"][c]["weight"]


def test_arabic_sentence_gives_the_same_three_fields():
    res = _parse(_ai(AR_CONTEXT, "إجادة لغة البرمجة Python", "إجادة لغة البرمجة Python."), AR_JD)
    assert res.requirements["context"] == {"location": "رام الله", "work_arrangement": "hybrid",
                                           "reports_to": "مدير عمليات تقنية المعلومات"}


# ── verification: only what the source wording establishes ──────────────────────────────────────────────────────────

def test_a_value_not_in_its_source_sentence_is_dropped_and_reported():
    ctx = dict(EN_CONTEXT, location="Nablus")
    res = _parse(_ai(ctx, "Python or PowerShell scripting", "The candidate must know Python or PowerShell scripting."), EN_JD)
    assert res.requirements["context"]["location"] is None
    assert res.requirements["context"]["reports_to"] == "IT Operations Manager"
    assert any(i.code == "context_not_verified" for i in res.review)


def test_context_is_dropped_when_its_source_sentence_is_not_in_the_job_description():
    """The fabricated sentence contains every value, so only the job-description check can refuse it."""
    ctx = dict(EN_CONTEXT, location="Gaza", work_arrangement="hybrid",
               source_text="The position is hybrid in Gaza and reports to the IT Operations Manager.")
    res = _parse(_ai(ctx, "Python or PowerShell scripting", "The candidate must know Python or PowerShell scripting."), EN_JD)
    assert "context" not in res.requirements
    assert any(i.code == "context_not_verified" for i in res.review)


def test_an_arrangement_word_not_in_the_source_is_dropped():
    ctx = dict(EN_CONTEXT, work_arrangement="remote")
    res = _parse(_ai(ctx, "Python or PowerShell scripting", "The candidate must know Python or PowerShell scripting."), EN_JD)
    assert res.requirements["context"]["work_arrangement"] is None


@pytest.mark.parametrize("bad", ["flexible", "Hybrid", 3, ["hybrid"]])
def test_an_arrangement_outside_the_vocabulary_is_dropped(bad):
    ctx = dict(EN_CONTEXT, work_arrangement=bad)
    res = _parse(_ai(ctx, "Python or PowerShell scripting", "The candidate must know Python or PowerShell scripting."), EN_JD)
    assert res.requirements["context"]["work_arrangement"] is None


def test_no_context_means_no_context_key_so_old_documents_stay_valid():
    res = _parse(_ai(None, "Python or PowerShell scripting", "The candidate must know Python or PowerShell scripting."), EN_JD)
    assert "context" not in res.requirements
    assert not validate_draft(res.requirements).errors and not validate_final(res.requirements).errors


# ── validation: a whitelist, nothing else ───────────────────────────────────────────────────────────────────────────

def _doc_with(context):
    res = _parse(_ai(None, "Python or PowerShell scripting", "The candidate must know Python or PowerShell scripting."), EN_JD)
    doc = copy.deepcopy(res.requirements)
    doc["context"] = context
    return doc


def test_a_valid_context_block_passes_validation():
    doc = _doc_with({"location": "Ramallah", "work_arrangement": "hybrid", "reports_to": None})
    assert not validate_final(doc).errors


@pytest.mark.parametrize("context", [
    {"location": "Ramallah", "work_arrangement": "hybrid", "reports_to": None, "salary": "5000"},
    {"location": "Ramallah", "work_arrangement": "hybrid"},
    {"location": "Ramallah", "work_arrangement": "flexible", "reports_to": None},
    {"location": "", "work_arrangement": None, "reports_to": None},
    {"location": 42, "work_arrangement": None, "reports_to": None},
    {"location": "x" * 201, "work_arrangement": None, "reports_to": None},
    "Ramallah",
])
def test_anything_outside_the_whitelist_is_refused(context):
    doc = _doc_with(context)
    assert any(i.code == "bad_context" for i in validate_draft(doc).errors)
    assert any(i.code == "bad_context" for i in validate_final(doc).errors)


def test_the_field_set_is_exactly_the_three_documented_fields():
    assert CONTEXT_FIELDS == ("location", "work_arrangement", "reports_to")


# ── save: the stored context is kept, whatever the client sends ──────────────────────────────────────────────────────

def test_a_save_keeps_the_stored_context_and_discards_a_client_block():
    stored = _doc_with({"location": "Ramallah", "work_arrangement": "hybrid", "reports_to": "IT Operations Manager"})
    incoming = copy.deepcopy(stored)
    incoming["context"] = {"location": "Elsewhere", "work_arrangement": "remote", "reports_to": None}
    assert carry_context(stored, incoming)["context"] == stored["context"]
    assert carry_server_owned(stored, incoming)["context"] == stored["context"]


def test_a_client_cannot_add_context_to_a_document_that_has_none():
    stored = _doc_with(None)
    stored.pop("context", None)
    incoming = copy.deepcopy(stored)
    incoming["context"] = {"location": "Ramallah", "work_arrangement": None, "reports_to": None}
    assert "context" not in carry_server_owned(stored, incoming)


# ── API: the three fields, read-only, and nothing else ──────────────────────────────────────────────────────────────

def test_the_projection_keeps_only_the_whitelisted_fields():
    doc = _doc_with({"location": "Ramallah", "work_arrangement": "hybrid", "reports_to": "IT Operations Manager"})
    projected = _project_document(doc)
    assert projected["context"] == {"location": "Ramallah", "work_arrangement": "hybrid", "reports_to": "IT Operations Manager"}
    assert "context" not in _project_document(_doc_with(None))


def test_the_api_model_accepts_documents_without_context_and_refuses_extra_fields():
    base = {"schema_version": 2, "categories": {c: {"weight": 0, "items": []} for c in CATEGORIES}}
    assert DocumentOut.model_validate(base).context is None
    with pytest.raises(ValidationError):
        ContextOut.model_validate({"location": None, "work_arrangement": None, "reports_to": None, "salary": 1})


# ── the pinned prompt: explicit only, and the context block is in its output format ─────────────────────────────────

def test_the_pinned_prompt_states_the_context_rule_and_its_output_shape():
    text = load_prompt()
    assert PROMPT_VERSION == "criteria_extraction_v2-2"
    assert '"context"' in text and '"source_text"' in text
    assert "Job context (not scored)" in text
    assert "not stated explicitly" in text or "explicitly" in text
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() != ""  # the loaded text is the file
    assert hashlib.sha256(pathlib.Path(__file__).resolve().parents[1].joinpath(
        "services/requirements_v2/extraction/prompts/criteria_extraction_v2-2.txt").read_bytes()).hexdigest() == PROMPT_SHA256


def test_the_superseded_prompt_file_is_gone():
    prompts = pathlib.Path(__file__).resolve().parents[1] / "services" / "requirements_v2" / "extraction" / "prompts"
    assert sorted(p.name for p in prompts.glob("*.txt")) == ["criteria_extraction_v2-2.txt"]
