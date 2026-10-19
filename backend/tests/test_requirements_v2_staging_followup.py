"""Staging follow-up (JOB-2026-0005): explicit OR alternatives, job context, and stated preferred requirements that were dropped.

What staging stored: Python and PowerShell as two required items quoting "must know Python or PowerShell scripting", no context, and
no item for "Azure or AWS experience is preferred" while the extraction reported success.

Root causes (reproduced below on the shapes a real answer has, against the parser as it was on main):
  * merge_explicit_alternatives skipped an item whose `experience` was an object (the model returns {"subject": null, "min_years": null}
    on non-experience items), skipped items that declared alternatives, and grouped only by an identical quotation, so two quotes of
    the same sentence that differed by a clause boundary were never one group.
  * The pinned prompt contradicted itself: rule 9 sent a reporting line / place of work only to the non-scoreable lists, the worked
    examples showed no `context` key, and nothing told the model that "X experience is preferred" is still an item.
  * Nothing compared the draft with the statements the description makes, so an omitted preferred sentence was a silent success.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from routers import job_requirements as R
from services import requirements_edit as E
from services.requirements_v2 import contract as C
from services.requirements_v2.extraction import build_request, parse_response
from services.requirements_v2.extraction.completeness import missed_context_statements, missed_preferred_statements
from services.requirements_v2.extraction.parser import merge_explicit_alternatives
from workers import requirements_v2_worker as worker

PS = "The candidate must know Python or PowerShell scripting."
JD = (
    "IT Systems Administrator\n"
    "The position is hybrid in Ramallah and reports to the IT Operations Manager.\n"
    "\n"
    "Requirements:\n"
    "- Bachelor's degree in Computer Science or Information Technology.\n"
    "- At least 3 years of experience in IT infrastructure administration.\n"
    f"- {PS}\n"
    "- Fluent in Arabic and English.\n"
    "\n"
    "Preferred:\n"
    "- ITIL certification is preferred.\n"
    "- Azure or AWS experience is preferred.\n"
)
HYBRID = "The position is hybrid in Ramallah and reports to the IT Operations Manager."
AZURE = "Azure or AWS experience is preferred."
NO_EXPERIENCE = {"subject": None, "min_years": None}


def _item(text, importance="required", source=None, *, cue=None, alternatives=None, experience=None, origin="stated"):
    return {"text": text, "importance": importance, "importance_cue": cue, "source_text": source or text,
            "origin": origin, "alternatives": alternatives, "experience": experience}


def _answer(*, skills, context=None, informational=(), certifications=None):
    cats = {c: [] for c in C.CATEGORIES}
    cats["skills"] = list(skills)
    cats["education"] = [_item("Bachelor's degree in Computer Science or Information Technology", source="Bachelor's degree in Computer Science or Information Technology.",
                               alternatives=["Computer Science", "Information Technology"])]
    cats["experience"] = [_item("At least 3 years of IT infrastructure administration", source="At least 3 years of experience in IT infrastructure administration.",
                                experience={"subject": "IT infrastructure administration", "min_years": 3})]
    cats["certifications"] = certifications if certifications is not None else [
        _item("ITIL certification", "preferred", "ITIL certification is preferred.", cue="is preferred")]
    out = {"scoreability": {"status": "scoreable", "reason": ""}, "categories": cats,
           "category_weights": {"skills": 40, "experience": 30, "education": 30, "certifications": 0, "soft_skills": 0,
                                "domain_knowledge": 0, "other_requirements": 0},
           "non_scoreable_requirements": [], "post_hiring_conditions": [], "informational_items": list(informational), "warnings": []}
    if context is not None:
        out["context"] = context
    return out


LANGUAGES = [_item("Arabic", source="Fluent in Arabic and English."), _item("English", source="Fluent in Arabic and English.")]
REPORTING = {"text": "Reports to the IT Operations Manager", "category": "reporting_line", "reason": "not a requirement", "source_text": HYBRID}
CONTEXT = {"location": "Ramallah", "work_arrangement": "hybrid", "reports_to": "IT Operations Manager", "source_text": HYBRID}
COMPLETE_SKILLS = [
    _item("Python or PowerShell scripting", source=PS, alternatives=["Python", "PowerShell"]), *LANGUAGES,
    _item("Azure or AWS", "preferred", AZURE, cue="is preferred", alternatives=["Azure", "AWS"])]


def _parse(answer, jd=JD, **kw):
    result = parse_response(json.dumps(answer, ensure_ascii=False), jd, "stop", **kw)
    assert result.status == "draft", result.errors
    return result


def _items(result, category="skills"):
    return result.requirements["categories"][category]["items"]


# ── 1-3. the stored Python / PowerShell shapes become ONE alternatives requirement ──────────────────────────────────────

def _python_powershell_shapes():
    plain = [_item("Python", source=PS), _item("PowerShell", source=PS)]
    empty_experience = [_item("Python", source=PS, experience=dict(NO_EXPERIENCE)), _item("PowerShell", source=PS, experience=dict(NO_EXPERIENCE))]
    declared = [_item("Python", source=PS, alternatives=["Python", "PowerShell"]), _item("PowerShell", source=PS, alternatives=["Python", "PowerShell"])]
    fragment = [_item("Python", source="must know Python or PowerShell scripting"), _item("PowerShell", source=PS)]
    both_empty = [_item("Python", source=PS, alternatives=[], experience=dict(NO_EXPERIENCE)), _item("PowerShell", source=PS, alternatives=[])]
    return {"plain": plain, "empty experience object": empty_experience, "each declares the alternatives": declared,
            "quotes differ by a clause boundary": fragment, "empty alternatives list": both_empty}


@pytest.mark.parametrize("shape", list(_python_powershell_shapes()))
def test_the_stored_python_powershell_shapes_become_one_required_alternatives_item(shape):
    items = _python_powershell_shapes()[shape]
    result = _parse(_answer(skills=[*items, *LANGUAGES]))
    skills = _items(result)
    group = [i for i in skills if i.get("alternatives") and "Python" in i["alternatives"]]
    assert len(group) == 1, [i["text"] for i in skills]
    assert group[0]["alternatives"] == ["Python", "PowerShell"]
    assert group[0]["importance"] == "required"
    assert "Python or PowerShell scripting" in group[0]["source_text"]
    assert [i["text"] for i in skills if i is not group[0]] == ["Arabic", "English"]       # "Arabic and English" is not an OR
    assert [i["weight"] for i in skills] == [34, 33, 33]                                     # three required items, weights over three
    assert sum(i["weight"] for i in skills) == 100


def test_the_python_powershell_shapes_were_not_merged_on_main():
    """Documents the defect: the unmerged shapes above are exactly what the previous merge left as two items (checked on main's
    behaviour by feeding the same items to the parser with merging turned off)."""
    result = _parse(_answer(skills=[*_python_powershell_shapes()["plain"], *LANGUAGES]), merge_alternatives=False)
    assert [i["text"] for i in _items(result)] == ["Python", "PowerShell", "Arabic", "English"]


def test_arabic_or_merges_the_same_way():
    jd = "يجب أن يجيد المرشح Python أو PowerShell.\nإجادة اللغة العربية والإنجليزية."
    source = "يجب أن يجيد المرشح Python أو PowerShell."
    answer = _answer(skills=[_item("Python", source=source), _item("PowerShell", source=source)])
    cats = answer["categories"]
    for name in ("education", "experience", "certifications"):
        cats[name] = []
    answer["category_weights"] = {"skills": 100}
    skills = _items(_parse(answer, jd))
    assert len(skills) == 1 and skills[0]["alternatives"] == ["Python", "PowerShell"]
    assert skills[0]["text"] == "Python أو PowerShell"


def test_explicit_and_requirements_are_never_merged():
    source = "The candidate must know Python and PowerShell scripting."
    items = [_item("Python", source=source), _item("PowerShell", source=source)]
    assert len(merge_explicit_alternatives(items, source)) == 2
    arabic = "يجب أن يجيد المرشح Python و PowerShell."
    assert len(merge_explicit_alternatives([_item("Python", source=arabic), _item("PowerShell", source=arabic)], arabic)) == 2


def test_an_option_next_to_an_and_stays_separate():
    source = "Must know Python or PowerShell and Docker."
    merged = merge_explicit_alternatives([_item("Python", source=source), _item("PowerShell", source=source), _item("Docker", source=source)], source)
    assert [(m["text"], m.get("alternatives")) for m in merged] == [("Python or PowerShell", ["Python", "PowerShell"]), ("Docker", None)]


def test_a_comma_list_ending_in_or_is_one_requirement_and_a_plain_list_is_not():
    either = "Experience with Azure, AWS or GCP."
    merged = merge_explicit_alternatives([_item(t, source=either) for t in ("Azure", "AWS", "GCP")], either)
    assert len(merged) == 1 and merged[0]["alternatives"] == ["Azure", "AWS", "GCP"]
    plain = "Tools: Docker, Git, Jenkins."
    assert len(merge_explicit_alternatives([_item(t, source=plain) for t in ("Docker", "Git", "Jenkins")], plain)) == 3


def test_merging_keeps_importance_cue_and_does_not_mix_importance():
    merged = merge_explicit_alternatives([_item("Azure", "preferred", AZURE, cue="is preferred"), _item("AWS", "preferred", AZURE, cue="is preferred")], AZURE)
    assert len(merged) == 1 and merged[0]["importance"] == "preferred" and merged[0]["importance_cue"] == "is preferred"
    mixed = [_item("Azure", "required", AZURE), _item("AWS", "preferred", AZURE, cue="is preferred")]
    assert len(merge_explicit_alternatives(mixed, AZURE)) == 2


# ── 6. Azure or AWS experience is preferred ────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("pair", [
    [_item("Azure", "preferred", AZURE, cue="is preferred"), _item("AWS", "preferred", AZURE, cue="is preferred")],
    [_item("Azure experience", "preferred", AZURE, cue="is preferred"), _item("AWS experience", "preferred", AZURE, cue="is preferred")],
    [_item("Azure", "preferred", AZURE, cue="is preferred", experience=dict(NO_EXPERIENCE)), _item("AWS", "preferred", AZURE, cue="preferred")],
])
def test_azure_or_aws_is_one_preferred_item_with_its_cue_and_no_warning(pair):
    result = _parse(_answer(skills=[*pair, *LANGUAGES]))
    group = [i for i in _items(result) if i["importance"] == "preferred"]
    assert len(group) == 1 and len(group[0]["alternatives"]) == 2 and group[0]["weight"] is None
    assert not result.requirements.get("classification_review", {}).get("warnings")


# ── 4-5. context: instructed, permitted, parsed, stored, projected, shown read-only ──────────────────────────────────────

def test_the_pinned_request_instructs_and_permits_context():
    request = build_request(JD)
    system = request["messages"][0]["content"]
    assert request["response_format"] == {"type": "json_object"}          # no response schema narrows the answer: `context` is permitted
    assert '"context": {"location"' in system and "required whenever the job description states" in system
    assert "ALSO recorded in \"context\"" in system                      # rule 9 no longer sends a reporting line only to informational_items
    assert system.count('"context":{"location"') >= 1                    # the worked examples show the key
    assert "ONE item" in system and "Python or PowerShell scripting" in system and "Completeness" in system


def test_the_acceptance_sentence_yields_the_three_context_values_and_nothing_scored():
    result = _parse(_answer(skills=COMPLETE_SKILLS, context=CONTEXT, informational=[REPORTING]))
    assert result.requirements["context"] == {"location": "Ramallah", "work_arrangement": "hybrid", "reports_to": "IT Operations Manager"}
    everything = json.dumps(result.requirements["categories"])
    for value in ("Ramallah", "hybrid", "IT Operations Manager"):
        assert value not in everything                                    # context never becomes a requirement and is never weighted
    assert result.readiness.can_proceed or result.readiness.state


# ── 7. the completeness safeguard is general ───────────────────────────────────────────────────────────────────────────

def test_a_preferred_sentence_without_an_item_is_reported_and_a_covered_one_is_not():
    incomplete = _parse(_answer(skills=[*LANGUAGES, _item("Python or PowerShell", source=PS, alternatives=["Python", "PowerShell"])]))
    assert incomplete.missed_preferred == (AZURE,)
    assert any(i.code == "preferred_statement_not_extracted" for i in incomplete.review)
    complete = _parse(_answer(skills=COMPLETE_SKILLS))
    assert complete.missed_preferred == ()


@pytest.mark.parametrize("jd,statement", [
    ("Role.\nTerraform or Ansible knowledge is a plus.\n", "Terraform or Ansible knowledge is a plus."),
    ("Role.\nIdeally, you hold a master's degree.\n", "Ideally, you hold a master's degree."),
    ("Role.\nNice to have:\n- Kubernetes\n- Helm\n", "Kubernetes"),
    ("المطلوب خبرة في المحاسبة.\nيفضل إتقان برنامج SAP.\n", "يفضل إتقان برنامج SAP."),
])
def test_the_safeguard_names_no_particular_tool(jd, statement):
    doc = C.empty_requirements()
    assert statement in missed_preferred_statements(jd, doc)


def test_headings_and_cue_only_lines_are_not_statements_and_conditions_count_as_covered():
    jd = "Role.\nPreferred:\nNice to have\nPreferred location is Ramallah.\n"
    doc = C.empty_requirements()
    assert missed_preferred_statements(jd, doc) == ("Preferred location is Ramallah.",)
    conditions = {"non_scoreable_requirements": [{"text": "Location Ramallah", "source_text": "Preferred location is Ramallah."}]}
    assert missed_preferred_statements(jd, doc, conditions) == ()


def test_missing_context_sentences_are_named_only_while_there_is_no_context():
    doc = C.empty_requirements()
    assert HYBRID in missed_context_statements(JD, doc)
    assert missed_context_statements(JD, {**doc, "context": {"location": "Ramallah", "work_arrangement": None, "reports_to": None}}) == ()


# ── 8. one deterministic end-to-end extraction regression on the complete description ──────────────────────────────────────

class _Resolved:
    model_name = "fixture"


def _run(monkeypatch, answers):
    sent = []

    async def fake_request(description, resolved, missed_statements=()):
        sent.append(tuple(missed_statements))
        return json.dumps(answers[min(len(sent), len(answers)) - 1], ensure_ascii=False), "stop"

    monkeypatch.setattr(worker, "_request_extraction", fake_request)
    return sent, lambda: asyncio.run(worker._extract_checked(JD, _Resolved(), ()))


def _flawed():
    """What staging produced: a split Python / PowerShell with empty experience objects, no Azure / AWS item, no context."""
    split = [_item("Python", source=PS, experience=dict(NO_EXPERIENCE)), _item("PowerShell", source=PS, experience=dict(NO_EXPERIENCE))]
    return _answer(skills=[*split, *LANGUAGES], informational=[REPORTING])


def test_the_complete_staging_description_extracts_everything_after_one_repair_request(monkeypatch):
    sent, run = _run(monkeypatch, [_flawed(), _answer(skills=COMPLETE_SKILLS, context=CONTEXT, informational=[REPORTING])])
    result = run()
    assert len(sent) == 2 and sent[0] == () and AZURE in sent[1] and HYBRID in sent[1]       # one repair request, naming the gaps
    request = build_request(JD, missed_statements=sent[1])
    assert AZURE in request["messages"][1]["content"] and "Review note" in request["messages"][1]["content"]

    doc, expectations = worker.finalise_document(worker.checked_document(result), JD)
    skills = doc["categories"]["skills"]["items"]
    python = next(i for i in skills if i.get("alternatives") == ["Python", "PowerShell"])
    azure = next(i for i in skills if i.get("alternatives") == ["Azure", "AWS"])
    assert python["importance"] == "required" and azure["importance"] == "preferred" and azure["weight"] is None
    assert [i["text"] for i in skills if i["importance"] == "required"] == ["Python or PowerShell scripting", "Arabic", "English"]
    assert doc["context"] == {"location": "Ramallah", "work_arrangement": "hybrid", "reports_to": "IT Operations Manager"}
    education = doc["categories"]["education"]["items"][0]
    experience = doc["categories"]["experience"]["items"][0]
    itil = doc["categories"]["certifications"]["items"][0]
    assert education["alternatives"] == ["Computer Science", "Information Technology"]
    assert experience["experience"] == {"subject": "IT infrastructure administration", "min_years": 3}
    assert itil["importance"] == "preferred" and doc["classification_review"]["warnings"] == []      # ITIL: Preferred, no warning
    assert sum(doc["categories"][c]["weight"] for c in C.CATEGORIES) == 100                          # weights stay valid
    assert doc.get("structural_review", {}).get("findings", []) == []                                # no split left for the detector
    assert expectations["expectations"] == [] or all(e["kind"] == "alternatives_together" for e in expectations["expectations"])

    view = E.build_edit_view(doc, job_id="job-1", revision=1, schema_version=2, original=result.original, jd_text=JD)
    response = R._edit_response(view).model_dump()
    assert response["document"]["context"] == {"location": "Ramallah", "work_arrangement": "hybrid", "reports_to": "IT Operations Manager"}
    assert response["review"]["readiness"]["can_proceed"] is True
    assert "context" in json.loads(R._edit_response(view).model_dump_json())["document"]


def test_a_complete_first_answer_makes_exactly_one_request(monkeypatch):
    sent, run = _run(monkeypatch, [_answer(skills=COMPLETE_SKILLS, context=CONTEXT, informational=[REPORTING])])
    assert run().ok and len(sent) == 1


def test_an_extraction_that_keeps_dropping_a_stated_preferred_sentence_fails_instead_of_succeeding(monkeypatch):
    sent, run = _run(monkeypatch, [_flawed()])
    with pytest.raises(worker.InvalidModelOutput, match="preferred_statement_missing"):
        run()
    assert len(sent) == 2                                                                         # one repair attempt, then the failure


def test_a_missing_context_alone_never_fails_the_extraction(monkeypatch):
    answer = _answer(skills=COMPLETE_SKILLS, informational=[REPORTING])                            # preferred covered, context absent
    sent, run = _run(monkeypatch, [answer])
    result = run()
    assert result.ok and "context" not in result.requirements and len(sent) == 2
