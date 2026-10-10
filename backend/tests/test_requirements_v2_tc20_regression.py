"""Regression fixture for JOB-2026-0121 "Technical Coordinator 20" (tests/fixtures/requirements_v2_technical_coordinator_20).

Passing tests pin what is verified: the identities, that the whole JD reaches the request, that every source text is
verbatim, and what the frozen pipeline does with the stored answer (it reports "ready").

The xfail(strict=True) tests state the definite gaps in expected_extraction.md. They fail with AssertionError until the
extraction covers them. A strict xfail that starts passing fails the suite, so its marker is then removed on purpose.
Offline runs prove nothing about model compliance; only a bounded, approved model evaluation can show that.
"""
import hashlib
import json
import pathlib

import pytest

FIX = pathlib.Path(__file__).parent / "fixtures" / "requirements_v2_technical_coordinator_20"
PROMPT_V23 = pathlib.Path(__file__).resolve().parents[1] / "prompt_candidates" / "criteria_extraction_v2-3" / "criteria_extraction_v2-3.txt"
HEADINGS = {"ABRS Service Implementation, Stabilization, and Enhancement", "Knowledge Transfer and Capacity Building"}
INTRO = "The Coordinator will support the implementation, stabilization, enhancement, and continuous improvement of ABRS services"
COMPETENCIES = (
    "Good understanding of business applications, digital platforms, and software support processes.",
    "Ability to support system testing, troubleshooting, issue tracking, and operational follow-up activities.",
    "Ability to communicate effectively with users, understand their requirements and issues, and support their resolution in coordination with relevant stakeholders.",
)


@pytest.fixture(scope="module")
def ev():
    return json.loads((FIX / "evidence.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def jd(ev):
    return ev["description"]


@pytest.fixture(scope="module")
def raw(ev):
    return json.loads(ev["pipeline"]["raw_response"]["text"])


@pytest.fixture(scope="module")
def items(raw):
    return [(cat, it) for cat, lst in raw["categories"].items() for it in lst]


def duty_lines(jd):
    """The duty lines of the Responsibilities section (16 in this JD), read from the JD text itself."""
    lines = [line.strip() for line in jd.split("\n")]
    start, end = lines.index("Responsibilities"), lines.index("Reporting and Supervision")
    body = [line for line in lines[start + 1:end] if line]
    return [line for line in body if line not in HEADINGS and not line.startswith(INTRO)]


def covering(items, phrase):
    return [(cat, it) for cat, it in items if phrase in it["source_text"]]


# ── passing: identities and the request ────────────────────────────────────────────────────────────────────────────────

def test_fixture_identities(ev, jd):
    assert hashlib.sha256(jd.encode("utf-8")).hexdigest() == ev["pipeline"]["job_description_sha256"]
    assert hashlib.sha256(ev["pipeline"]["raw_response"]["text"].encode("utf-8")).hexdigest() == ev["pipeline"]["raw_response"]["sha256"]
    assert hashlib.sha256(PROMPT_V23.read_bytes()).hexdigest() == ev["usage"][0]["metadata"]["prompt_sha256"]


def test_call_metadata_rules_out_truncation(ev):
    use = ev["usage"][0]
    assert use["model"] == use["metadata"]["requested_model"] == use["metadata"]["returned_model"] == "gpt-4o-mini-2024-07-18"
    assert use["metadata"]["finish_reason"] == "stop"
    assert use["metadata"]["settings"]["max_tokens"] == 6000 and use["metadata"]["settings"]["temperature"] == 0.1
    assert use["completion_tokens"] < use["metadata"]["settings"]["max_tokens"]


def test_request_carries_the_whole_jd(jd):
    from services.requirements_v2.extraction.prompt import build_user_message
    message = build_user_message(jd, None)
    assert jd in message, "the worker's user message must carry the JD verbatim, with no truncation"


def test_stored_source_texts_are_verbatim_and_grounded(items, jd):
    for cat, it in items:
        assert it["source_text"] in jd, (cat, it["text"])
        if it["importance_cue"] is not None:
            assert it["importance_cue"] in jd, (cat, it["text"])


def test_the_duty_lines_of_the_jd_are_sixteen(jd):
    assert len(duty_lines(jd)) == 16


def test_the_frozen_pipeline_reports_ready_for_the_stored_answer(ev, jd):
    """Documents what the system does with this answer today: the frozen pipeline accepts it with no reasons."""
    from parser_candidates.requirements_v2_pipeline_1 import extract
    doc = extract(jd, ev["pipeline"]["raw_response"]["text"], finish_reason="stop",
                  extraction_prompt={"version": "criteria_extraction_v2-3", "sha256": ev["usage"][0]["metadata"]["prompt_sha256"]},
                  require_classification_acknowledgment=True)
    assert doc["status"] == "draft" and doc["ok"] is True and doc["errors"] == []
    assert doc["readiness"]["state"] == "ready" and doc["readiness"]["reasons"] == []


# ── strict expected failures: the definite gaps in expected_extraction.md ─────────────────────────────────────────────

@pytest.mark.xfail(strict=True, raises=AssertionError, reason="D1: no from_responsibilities items (rule 7)")
def test_every_responsibility_is_an_experience_item(items, jd):
    missing = [d for d in duty_lines(jd) if not any(it["origin"] == "from_responsibilities" and d in it["source_text"] for _, it in items)]
    assert missing == []


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="D2-D4: explicit competency lines omitted (rule 1)")
def test_every_competency_line_is_extracted(items):
    missing = [line for line in COMPETENCIES if not covering(items, line)]
    assert missing == []


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="D5: OR options of the experience line are not alternatives (rules 4, 6)")
def test_experience_or_options_are_alternatives(items):
    (cat, it), = [(c, i) for c, i in items if "ICT systems support" in i["source_text"]]
    assert cat == "experience"
    assert sorted(it["alternatives"] or []) == sorted(["ICT systems support", "business applications", "digital platforms", "software implementation projects"])


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="D6: OR options of the familiarity line are not alternatives (rule 4)")
def test_familiarity_or_options_are_alternatives(items):
    (cat, it), = covering(items, "Familiarity with business process documentation")
    assert it["alternatives"] and "enterprise applications" in it["alternatives"]


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="D7: local business knowledge is in experience, not domain_knowledge (rule 5)")
def test_local_business_knowledge_is_domain_knowledge(items):
    (cat, it), = covering(items, "Knowledge of the local business and regulatory environment")
    assert cat == "domain_knowledge"


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="D8: work location is not a non-scoreable condition (rule 9)")
def test_work_location_is_non_scoreable(raw):
    assert any("Ramallah" in c["source_text"] for c in raw["non_scoreable_requirements"])


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="D9: reporting statements are not informational items (rule 9)")
def test_reporting_arrangements_are_informational(raw):
    assert any("submit deliverables" in c["source_text"] and c["category"] == "reporting_line" for c in raw["informational_items"])


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="D10: the three-item AND list is one item (rule 4)")
def test_and_list_of_soft_skills_is_split(items):
    texts = [it["text"].lower() for cat, it in items if cat == "soft_skills"]
    assert any("communication" in t for t in texts) and any("coordination" in t for t in texts) and any("teamwork" in t for t in texts)
    assert not any("communication" in t and "teamwork" in t for t in texts)
