"""A Preferred classification the job description states in its own words needs no acknowledgment.

Staging showed "ITIL certification is marked Preferred, but the job description does not clearly say so" for the sentence
"ITIL certification is preferred." Cause: the check trusted only the model's cue and looked for it inside the quoted words or before
them. A model that quoted the short "ITIL certification" (the cue follows it), or gave no cue, or a different cue, raised a warning
for a sentence that plainly says "preferred". The job description's own approved optional wording now establishes the
classification (extraction/cues.py); a sentence that does not state it, or that also calls the requirement required, still warns.
"""
from __future__ import annotations

import json

import pytest

from services.requirements_v2 import contract as C
from services.requirements_v2.acknowledgment import source_establishes_preferred
from services.requirements_v2.extraction import parse_response
from services.requirements_v2.extraction.cues import jd_marks_preferred
from services.requirements_v2.readiness import compute_readiness
from workers import requirements_v2_worker as worker

ITIL = "ITIL certification is preferred."


def _parse(jd, *, source, cue, text="ITIL certification", category="certifications", required=("Python",)):
    cats = {c: [] for c in C.CATEGORIES}
    cats["skills"] = [{"text": t, "importance": "required", "source_text": f"{t} is required.", "origin": "stated"} for t in required]
    cats[category] = [{"text": text, "importance": "preferred", "importance_cue": cue, "source_text": source, "origin": "stated"}]
    raw = {"categories": cats, "category_weights": {"skills": 100}, "scoreability": {"status": "scoreable"}}
    return parse_response(json.dumps(raw, ensure_ascii=False), jd, "stop", id_factory=worker.stable_id_factory(jd))


def _warnings(result):
    return [w["code"] for w in (result.requirements.get("classification_review") or {}).get("warnings", [])]


def _item(result, category="certifications"):
    return result.requirements["categories"][category]["items"][0]


LAYOUTS = {
    "a list entry": "Requirements:\n- Python is required.\n- ITIL certification is preferred.\n",
    "under a Preferred heading": "Python is required.\nPreferred:\n- ITIL certification is preferred.\n",
    "in a paragraph": "We need Python. Python is required. ITIL certification is preferred. Apply now.\n",
    "first line": "ITIL certification is preferred.\nPython is required.\n",
}
# What the model may return for that sentence: a full quotation, a short one (the cue follows it), no cue, the wrong cue.
MODEL_SHAPES = {
    "full quotation, cue 'is preferred'": (ITIL, "is preferred"),
    "full quotation, cue 'preferred'": (ITIL, "preferred"),
    "full quotation, no cue": (ITIL, None),
    "short quotation, cue 'is preferred'": ("ITIL certification", "is preferred"),
    "short quotation, cue 'preferred'": ("ITIL certification", "preferred"),
    "short quotation, no cue": ("ITIL certification", None),
    "short quotation, a cue the description does not use": ("ITIL certification", "nice to have"),
}


@pytest.mark.parametrize("shape", list(MODEL_SHAPES))
@pytest.mark.parametrize("layout", list(LAYOUTS))
def test_itil_certification_is_preferred_needs_no_acknowledgment(layout, shape):
    source, cue = MODEL_SHAPES[shape]
    result = _parse(LAYOUTS[layout], source=source, cue=cue)
    assert result.ok, result.errors
    assert _warnings(result) == []
    item = _item(result)
    assert item["importance"] == "preferred" and item["weight"] is None           # still Preferred, still unweighted


def test_the_document_is_ready_without_any_acknowledgment():
    jd = LAYOUTS["a list entry"]
    result = _parse(jd, source="ITIL certification", cue=None)
    doc, _ = worker.finalise_document(worker.checked_document(result), jd)
    readiness = compute_readiness(doc, require_classification_acknowledgment=True, original=result.original, jd_text=jd)
    assert readiness.state == "ready" and readiness.can_proceed
    assert doc["classification_review"] == {"warnings": [], "acknowledgments": []}


# ── every approved form of optional wording, English and Arabic ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("sentence", [
    "ITIL certification is preferred.", "ITIL certification is desirable.", "ITIL certification is advantageous.",
    "ITIL certification is optional.", "ITIL certification is nice to have.", "ITIL certification is nice-to-have.",
    "ITIL certification is a plus.", "ITIL certification is an advantage.", "ITIL certification is an asset.",
    "Ideally, ITIL certification.", "ITIL certification is preferred but not required.", "ITIL certification is preferably held.",
])
def test_each_approved_english_form_establishes_the_classification(sentence):
    jd = f"Python is required.\n{sentence}\n"
    result = _parse(jd, source="ITIL certification", cue=None)
    assert _warnings(result) == []


@pytest.mark.parametrize("sentence,item", [
    ("يفضل الحصول على شهادة ITIL.", "شهادة ITIL"),
    ("شهادة ITIL مفضلة.", "شهادة ITIL"),
    ("شهادة ITIL ميزة إضافية.", "شهادة ITIL"),
    ("شهادة ITIL اختيارية.", "شهادة ITIL"),
    ("شهادة ITIL أفضلية.", "شهادة ITIL"),
])
def test_each_approved_arabic_form_establishes_the_classification(sentence, item):
    jd = f"يجب أن يجيد المرشح بايثون.\n{sentence}\n"
    result = _parse(jd, source=item, cue=None, text=item, required=())
    assert _warnings(result) == []
    assert _item(result)["importance"] == "preferred"


# ── what is still ambiguous or unsupported keeps its warning (fail closed) ──────────────────────────────────────────────

AMBIGUOUS = {
    "the sentence is not optional wording": ("Python is required.\nITIL certification is valued.\n", "ITIL certification", "preferred"),
    "no cue anywhere, none from the model": ("Python is required.\nITIL certification.\n", "ITIL certification", None),
    "the cue is in another sentence": ("Python is required.\nITIL certification. Docker is preferred.\n", "ITIL certification", "preferred"),
    "the cue is on another line, with no heading": ("Python is required.\nITIL certification\nDocker is preferred\n", "ITIL certification", None),
    "the same sentence also says required": ("Python is required.\nITIL certification is required and Docker is preferred.\n", "ITIL certification", "preferred"),
    "the sentence says must": ("Python is required.\nYou must hold ITIL certification, which is preferred.\n", "ITIL certification", None),
    "the quotation is not in the description": ("Python is required.\nITIL certification is preferred.\n", "wording that is not there", "preferred"),
    "a Preferred heading belongs to a different section": ("Preferred:\n- Docker\n\nRequirements:\n- ITIL certification\n", "ITIL certification", "preferred"),
}


@pytest.mark.parametrize("case", list(AMBIGUOUS))
def test_an_unclear_or_unsupported_preferred_classification_still_requires_acknowledgment(case):
    jd, source, cue = AMBIGUOUS[case]
    result = _parse(jd, source=source, cue=cue)
    assert result.ok
    assert _warnings(result), case
    doc, _ = worker.finalise_document(worker.checked_document(result), jd)
    readiness = compute_readiness(doc, require_classification_acknowledgment=True, original=result.original, jd_text=jd)
    assert readiness.state == "needs_classification_review" and not readiness.can_proceed


def test_the_classification_is_never_changed_only_checked():
    jd = "Python is required.\nITIL certification is valued.\n"
    result = _parse(jd, source="ITIL certification", cue="preferred")
    assert _item(result)["importance"] == "preferred"                               # kept as the model gave it, and flagged


def test_a_required_item_is_not_affected():
    jd = "Python is required.\nITIL certification is preferred.\n"
    result = _parse(jd, source="ITIL certification", cue=None, required=("Python",))
    python = result.requirements["categories"]["skills"]["items"][0]
    assert python["importance"] == "required" and python["weight"] == 100


# ── the helper and the reopened-warning check use the same standard ─────────────────────────────────────────────────────

def test_jd_marks_preferred_reads_both_sides_of_the_quoted_words_and_the_heading():
    jd = "Requirements:\n- ITIL certification is preferred.\n\nNice to have:\n- Docker\n"
    start = jd.index("ITIL certification")
    assert jd_marks_preferred(jd, (start, start + len("ITIL certification")))                    # the cue follows the quoted words
    docker = jd.index("Docker")
    assert jd_marks_preferred(jd, (docker, docker + len("Docker")))                              # the heading above carries the cue
    assert not jd_marks_preferred(jd, None)                                                      # source not located: not established
    python = "Requirements:\n- Python\n"
    assert not jd_marks_preferred(python, (python.index("Python"), python.index("Python") + 6))


def test_a_stored_warning_clears_when_the_items_own_source_states_the_classification():
    warning = {"evidence": {"cue": None}}
    assert source_establishes_preferred({"source_text": "ITIL certification is preferred."}, warning)
    assert source_establishes_preferred({"source_text": "ITIL certification is preferred but not required"}, warning)
    assert not source_establishes_preferred({"source_text": "ITIL certification is valued"}, warning)
    assert not source_establishes_preferred({"source_text": "ITIL certification is required and Docker is preferred"}, warning)
    assert not source_establishes_preferred({"source_text": None}, warning)
