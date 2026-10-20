"""An optional cue binds to ONE requirement, not to every requirement in its sentence.

The first version of the staging fix accepted a cue anywhere in the item's sentence, so "Python, Docker is a plus." would have
established Python as Preferred too. The cue is now bound by structure (extraction/cues.py): the sentence is cut into clauses only where both
sides already carry their own optional or required wording, a clause that says required never establishes Preferred, and the cue binds
when it is inside the quoted words, at the start of the clause ahead of the requirement, or after it in a clause that is not a comma list.
Where the cue cannot be tied to one item, the classification keeps its warning and the recruiter decides. No grammar is guessed.
"""
from __future__ import annotations

import json

import pytest

from services.requirements_v2 import contract as C
from services.requirements_v2.extraction import parse_response
from services.requirements_v2.extraction.cues import jd_marks_preferred
from services.requirements_v2.readiness import compute_readiness
from workers import requirements_v2_worker as worker


def _classification_warnings(jd, quoted, text=None, cue="preferred", category="skills", required_item=None):
    """The warning codes for ONE item the model called Preferred, quoting `quoted` from `jd`."""
    cats = {c: [] for c in C.CATEGORIES}
    cats[category] = [{"text": text or quoted, "importance": "preferred", "importance_cue": cue, "source_text": quoted, "origin": "stated"}]
    if required_item:
        cats["soft_skills"] = [{"text": required_item, "importance": "required", "source_text": required_item, "origin": "stated"}]
    raw = {"categories": cats, "category_weights": {"soft_skills": 100} if required_item else {}, "scoreability": {"status": "scoreable"}}
    result = parse_response(json.dumps(raw, ensure_ascii=False), jd, "stop", id_factory=worker.stable_id_factory(jd))
    assert result.ok, result.errors
    return [w["code"] for w in (result.requirements.get("classification_review") or {}).get("warnings", [])]


# (case, job description, the words the model quotes for the item, expected: True = no acknowledgment needed)
CASES = [
    # the staging sentence: the stored quotation may be only the short form
    ("itil short quote", "ITIL certification is preferred.", "ITIL certification", True),
    ("itil full sentence", "ITIL certification is preferred.", "ITIL certification is preferred.", True),
    # a list whose cue closes it: it cannot be told apart from "A, B are preferred", so it is not bound to either item
    ("list then cue: first item", "Python, Docker is a plus.", "Python", False),
    ("list then cue: last item, short quote", "Python, Docker is a plus.", "Docker", False),
    ("list then cue: last item, quote carries the cue", "Python, Docker is a plus.", "Docker is a plus", True),
    ("list then cue: quote spans the whole list", "Python, Docker is a plus.", "Python, Docker is a plus", False),
    # two clauses with their own wording
    ("semicolon: the required one", "Python is required; Docker is preferred.", "Python", False),
    ("semicolon: the preferred one", "Python is required; Docker is preferred.", "Docker", True),
    ("semicolon: quote spans both clauses", "Python is required; Docker is preferred.", "Python is required; Docker is preferred", False),
    ("and: the required one", "Python is required and Docker is optional.", "Python", False),
    ("and: the optional one", "Python is required and Docker is optional.", "Docker", True),
    ("and, reversed: the required one", "Docker is optional and Python is required.", "Python", False),
    ("and, reversed: the optional one", "Docker is optional and Python is required.", "Docker", True),
    ("comma: the required one", "Python is required, Docker is optional.", "Python", False),
    ("comma: the optional one", "Python is required, Docker is optional.", "Docker", True),
    ("but: the required one", "Python is required but Docker is a plus.", "Python", False),
    ("but: the optional one", "Python is required but Docker is a plus.", "Docker", True),
    # one cue for terms joined only by and / or
    ("coordinated: first", "Python and Docker are preferred.", "Python", True),
    ("coordinated: second", "Python and Docker are preferred.", "Docker", True),
    ("alternatives", "Python or Docker is a plus.", "Docker", True),
    # a cue ahead of the requirement
    ("leading adverb: first", "Preferably Docker, Kubernetes.", "Docker", True),
    ("leading adverb: second", "Preferably Docker, Kubernetes.", "Kubernetes", True),
    ("leading label: first", "Preferred: Docker, Kubernetes.", "Docker", True),
    ("leading label: second", "Preferred: Docker, Kubernetes.", "Kubernetes", True),
    ("a cue in the middle of a list", "Python, preferably Docker.", "Python", False),
    ("a cue in the middle of a list, after it", "Python, preferably Docker.", "Docker", False),
    # preferred but not required is one clause
    ("not required", "ITIL certification is preferred but not required.", "ITIL certification", True),
    # arabic
    ("ar: required then optional, required item", "بايثون مطلوب؛ دوكر اختياري.", "بايثون", False),
    ("ar: required then optional, optional item", "بايثون مطلوب؛ دوكر اختياري.", "دوكر", True),
    ("ar: standalone conjunction, required item", "بايثون مطلوب و دوكر اختياري.", "بايثون", False),
    ("ar: standalone conjunction, optional item", "بايثون مطلوب و دوكر اختياري.", "دوكر", True),
    ("ar: glued conjunction is not cut, so neither is bound", "بايثون مطلوب ودوكر اختياري.", "دوكر", False),
    ("ar: coordinated terms, one cue", "بايثون ودوكر مفضلان.", "بايثون", True),
    ("ar: list then cue", "بايثون، دوكر ميزة إضافية.", "بايثون", False),
    ("ar: leading cue", "يفضل دوكر وكوبرنيتس.", "دوكر", True),
    ("ar: cue after the term", "شهادة ITIL مفضلة.", "شهادة ITIL", True),
    # headings
    ("heading: first entry", "Preferred qualifications:\n- Docker\n- ITIL certification\n", "Docker", True),
    ("heading: second entry", "Preferred qualifications:\n- Docker\n- ITIL certification\n", "ITIL certification", True),
    ("heading: the nearest one wins", "Required qualifications:\n- Python\nPreferred qualifications:\n- Docker\n", "Docker", True),
    ("heading: a required section above", "Required qualifications:\n- Python\nPreferred qualifications:\n- Docker\n", "Python", False),
    ("heading: the entry itself says required", "Preferred qualifications:\n- Python is required\n", "Python", False),
    # a cue elsewhere does not apply
    ("another sentence", "Python. Docker is preferred.", "Python", False),
    ("another line without a heading", "Python\nDocker is preferred\n", "Python", False),
    ("another section's heading", "Preferred:\n- Docker\n\nRequirements:\n- ITIL certification\n", "ITIL certification", False),
]


@pytest.mark.parametrize("case,jd,quoted,established", CASES, ids=[c[0] for c in CASES])
def test_an_optional_cue_binds_only_to_its_own_requirement(case, jd, quoted, established):
    codes = _classification_warnings(jd, quoted)
    assert (codes == []) is established, (case, codes)


@pytest.mark.parametrize("cue", [None, "preferred", "is a plus", "nice to have"])
def test_the_models_own_cue_never_widens_the_binding(cue):
    """Whatever cue the model names, a requirement the wording does not bind to it keeps its warning."""
    assert _classification_warnings("Python, Docker is a plus.", "Python", cue=cue)
    assert _classification_warnings("Python is required; Docker is preferred.", "Python", cue=cue)
    assert _classification_warnings("Python. Docker is preferred.", "Python", cue=cue)
    assert _classification_warnings("Python is required and Docker is optional.", "Docker", cue=cue) == []


def test_required_and_optional_wording_do_not_leak_between_items():
    """The real extraction of "Python is required and Docker is optional.": each item keeps what the model gave it, and the document is
    ready with no acknowledgment, because Docker's optional wording is its own."""
    jd = "Python is required and Docker is optional."
    cats = {c: [] for c in C.CATEGORIES}
    cats["skills"] = [
        {"text": "Python", "importance": "required", "importance_cue": None, "source_text": "Python is required", "origin": "stated"},
        {"text": "Docker", "importance": "preferred", "importance_cue": "optional", "source_text": "Docker is optional", "origin": "stated"}]
    raw = {"categories": cats, "category_weights": {"skills": 100}, "scoreability": {"status": "scoreable"}}
    result = parse_response(json.dumps(raw), jd, "stop", id_factory=worker.stable_id_factory(jd))
    doc, _ = worker.finalise_document(worker.checked_document(result), jd)
    python, docker = doc["categories"]["skills"]["items"]
    assert (python["importance"], python["weight"]) == ("required", 100)
    assert (docker["importance"], docker["weight"]) == ("preferred", None)
    assert doc["classification_review"]["warnings"] == []
    assert compute_readiness(doc, require_classification_acknowledgment=True, original=result.original, jd_text=jd).state == "ready"


def test_a_requirement_that_cannot_be_tied_to_its_cue_blocks_scoring_until_acknowledged():
    jd = "Python is required.\nPython, Docker is a plus.\n"
    cats = {c: [] for c in C.CATEGORIES}
    cats["skills"] = [{"text": "Python", "importance": "required", "importance_cue": None, "source_text": "Python is required", "origin": "stated"}]
    cats["domain_knowledge"] = [{"text": "Docker", "importance": "preferred", "importance_cue": "is a plus", "source_text": "Docker", "origin": "stated"}]
    raw = {"categories": cats, "category_weights": {"skills": 100}, "scoreability": {"status": "scoreable"}}
    result = parse_response(json.dumps(raw), jd, "stop", id_factory=worker.stable_id_factory(jd))
    doc, _ = worker.finalise_document(worker.checked_document(result), jd)
    readiness = compute_readiness(doc, require_classification_acknowledgment=True, original=result.original, jd_text=jd)
    assert readiness.state == "needs_classification_review" and not readiness.can_proceed
    assert doc["categories"]["domain_knowledge"]["items"][0]["importance"] == "preferred"      # checked, never changed


def test_jd_marks_preferred_refuses_a_quotation_that_runs_across_clauses_or_is_not_located():
    jd = "Python is required; Docker is preferred."
    assert not jd_marks_preferred(jd, (0, len(jd)))
    assert not jd_marks_preferred(jd, None)
    docker = jd.index("Docker")
    assert jd_marks_preferred(jd, (docker, docker + len("Docker")))
    assert not jd_marks_preferred(jd, (0, len("Python")))


@pytest.mark.parametrize("jd,quoted,cue", [
    ("Docker.\n", "Docker", "Docker"),                                           # the item's own word as its "cue"
    ("Python is a core skill.\n", "Python", "core skill"),                      # a phrase the description has but that is not optional wording
    ("ITIL certification is valued.\n", "ITIL certification", "valued"),
    ("Docker is highly valued.\n", "Docker", "Docker is highly valued"),       # the whole quotation as the cue
])
def test_a_cue_the_model_names_establishes_nothing(jd, quoted, cue):
    """Only the job description's approved optional wording establishes a Preferred classification. A cue that merely appears in the
    description, or is the requirement's own words, does not."""
    assert _classification_warnings(jd, quoted, cue=cue)
