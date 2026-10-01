"""
P0-02a — CANNOT_DETERMINE, strict D-01 validation, verified/upper scores.

Covers:
- D-01 status contract validation (every rule fails on its own, no coercion)
- single repair call per attempt, call counts, summary call only after a valid response
- D-01 client limits (max_retries=1, timeout=120 s)
- F-01 verified/upper credits, pending points/worth, blocking gaps, absent floor
- Phase 3: relevance-only disagreement → CD; established shortfall stays
- recommendation rule (all branches) and golden candidates X, A, Y, B, C, Z, D, E
- serialiser cd_reason round trip; historical v1/v2 payloads read as fully verified
- candidate ordering SQL, export columns/labels, migration 105
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import services.llm_criteria_mapper as mapper_mod
from services.llm_criteria_mapper import (
    CriteriaMappingResponseError,
    LLMCriteriaMapper,
    LLMCriterionAssessment,
    LLMMatchResult,
    _apply_skill_family_upgrade,
    _parse_llm_response,
    _response_validation_errors,
    _validate_assessment,
)
from services.deterministic_scoring import (
    DeterministicScoringConfig,
    DeterministicScoringEngine,
    decision_from_signal,
    deterministic_score_to_dict,
    recommendation_from_decisions,
)
from services.evidence_serialiser import (
    extract_flat_columns_from_det_score_json,
    llm_matchresult_from_dict,
    llm_matchresult_to_dict,
)
from services.candidate_ordering import CANDIDATE_SORT_FIELDS, candidate_order_by
from services.scoring_method import DETERMINISTIC, DETERMINISTIC_V2, verification_summary

BACKEND = Path(__file__).resolve().parent.parent
PROMPT_META = dict(prompt_code="recruitment.criteria_mapping", prompt_version="10", llm_model="gpt-4o-mini")
CD = "CANNOT_DETERMINE"


# ── helpers ──────────────────────────────────────────────────────────────────

def _item(**over) -> dict:
    d = {
        "criterion_text": "Minimum 2 years of relevant experience",
        "dimension": "experience",
        "required": True,
        "status": "MATCHED",
        "cd_reason": None,
        "confidence": 0.8,
        "supporting_evidence": ["Teacher 2015-2021"],
        "match_reason": "Six years as a teacher.",
        "match_type": "direct",
        "criterion_class": "experience",
        "risk_flags": [],
    }
    d.update(over)
    return d


def _cd_item(**over) -> dict:
    d = _item(status=CD, cd_reason="relevance_unverified",
              match_reason="Six years total; education-sector relevance not stated.")
    d.update(over)
    return d


def _raw(*items) -> str:
    return json.dumps({"assessments": list(items)})


# ═════════════════════════════════════════════════════════════════════════════
# 1. D-01 validation
# ═════════════════════════════════════════════════════════════════════════════

class TestStatusContractValidation:

    @pytest.mark.parametrize("status", ["MATCHED", "PARTIAL", "ABSENT", CD])
    def test_all_four_states_accepted(self, status):
        item = _cd_item() if status == CD else _item(
            status=status,
            supporting_evidence=[] if status == "ABSENT" else ["Teacher 2015-2021"],
            match_type="missing" if status == "ABSENT" else "direct",
        )
        assert _validate_assessment(item) is None
        (a,), _ = _parse_llm_response(_raw(item), [], **PROMPT_META)
        assert a.status == status
        assert a.cd_reason == ("relevance_unverified" if status == CD else None)

    @pytest.mark.parametrize("reason", ["relevance_unverified", "detail_missing", "ambiguous", "conflicting"])
    def test_every_cd_reason_accepted(self, reason):
        assert _validate_assessment(_cd_item(cd_reason=reason)) is None

    @pytest.mark.parametrize("item, fragment", [
        ("not a dict", "not a JSON object"),
        (_item(status=None), "status None"),
        (_item(status="UNCLEAR"), "status 'UNCLEAR'"),
        (_item(status="matched"), "status 'matched'"),
        (_item(criterion_text=""), "criterion_text"),
        (_item(criterion_text=None), "criterion_text"),
        (_item(supporting_evidence=[]), "MATCHED without supporting_evidence"),
        (_item(supporting_evidence=["", "  "]), "MATCHED without supporting_evidence"),
        (_item(status="PARTIAL", supporting_evidence=[]), "PARTIAL without supporting_evidence"),
        (_cd_item(supporting_evidence=[]), "CANNOT_DETERMINE without supporting_evidence"),
        (_cd_item(match_reason=""), "CANNOT_DETERMINE without match_reason"),
        (_cd_item(cd_reason=None), "invalid cd_reason None"),
        (_cd_item(cd_reason="extraction_gap"), "invalid cd_reason 'extraction_gap'"),
        (_item(cd_reason="ambiguous"), "cd_reason 'ambiguous' set on MATCHED"),
        (_item(status="ABSENT", supporting_evidence=[], cd_reason="detail_missing"), "set on ABSENT"),
        (_item(confidence="0.8"), "confidence '0.8'"),
        (_item(confidence=1.2), "confidence 1.2"),
        (_item(confidence=-0.1), "confidence -0.1"),
        (_item(confidence=None), "confidence None"),
        (_item(match_reason=""), "MATCHED without match_reason"),
    ])
    def test_each_rule_fails_without_coercion(self, item, fragment):
        err = _validate_assessment(item)
        assert err is not None and fragment in err
        # and the whole response is rejected — never coerced to ABSENT or CD
        with pytest.raises(CriteriaMappingResponseError, match=re.escape(fragment)):
            _parse_llm_response(_raw(item), [], **PROMPT_META)

    def test_one_invalid_item_invalidates_the_response(self):
        errors = _response_validation_errors(_raw(_item(), _item(status="UNCLEAR"), _item()))
        assert len(errors) == 1 and errors[0].startswith("assessment[1]")
        with pytest.raises(CriteriaMappingResponseError):
            _parse_llm_response(_raw(_item(), _item(status="UNCLEAR")), [], **PROMPT_META)

    def test_response_level_failures(self):
        assert _response_validation_errors("{bad") == ["invalid JSON"]
        assert _response_validation_errors("[]") == ["response is not a JSON object"]
        assert _response_validation_errors("{}") == ["missing 'assessments' array"]
        assert _response_validation_errors('{"assessments": []}') == ["empty 'assessments' array"]

    def test_all_absent_response_is_valid(self):
        absent = _item(status="ABSENT", supporting_evidence=[], match_type="missing", confidence=0.0)
        assert _response_validation_errors(_raw(absent, absent)) == []

    def test_match_type_missing_on_matched_tolerated_with_flag(self):
        (a,), _ = _parse_llm_response(_raw(_item(match_type="missing")), [], **PROMPT_META)
        assert a.status == "MATCHED"
        assert "match_type_missing" in a.risk_flags

    def test_absent_may_quote_a_non_matching_fact(self):
        item = _item(status="ABSENT", supporting_evidence=["Diploma in Arts"], match_type="missing")
        assert _validate_assessment(item) is None

    def test_skill_family_upgrade_leaves_cd_alone(self):
        def a(text, status, ev):
            return LLMCriterionAssessment(
                criterion_text=text, dimension="skills", required=True, status=status,
                confidence=0.6, supporting_evidence=ev, match_reason="r",
                match_type="direct", criterion_class="flexible",
                cd_reason="detail_missing" if status == CD else None,
            )
        items = [a("MS Office", "MATCHED", ["Microsoft Office suite"]), a("Microsoft Excel", CD, ["Office"])]
        assert _apply_skill_family_upgrade(items) == 0
        assert items[1].status == CD and items[1].cd_reason == "detail_missing"


# ═════════════════════════════════════════════════════════════════════════════
# 2. Repair call, call budget, summary call, client limits
# ═════════════════════════════════════════════════════════════════════════════

ANALYSIS_JSON = {"skills": {"required": ["MS Office"], "preferred": []}}


def _client(*contents):
    responses = []
    for c in contents:
        r = MagicMock()
        r.choices = [MagicMock(message=MagicMock(content=c))]
        responses.append(r)
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=responses)
    return client


async def _assess(client, summary=None, prompt=None):
    from services.cv_evidence import CVFacts
    summary = summary or AsyncMock(return_value=None)
    with patch.object(mapper_mod, "_get_mapper_client", return_value=client), \
         patch("services.ai_service.load_active_prompt", AsyncMock(return_value=prompt)), \
         patch("services.ai_service._apply_security_hardening", side_effect=lambda c: c), \
         patch.object(mapper_mod, "_generate_qualitative_summary", summary):
        return await LLMCriteriaMapper().assess(
            cv_facts=CVFacts(language="en", total_char_count=10),
            analysis_json=ANALYSIS_JSON, raw_cv_text="MS Office user. " * 5,
            application_id="app-1", job_id="job-1", db=None,
        )


_VALID = _raw(_item(criterion_text="MS Office", dimension="skills", criterion_class="flexible",
                    supporting_evidence=["Excel, Word"]))
_VALID_CD = _raw(_cd_item(criterion_text="MS Office", dimension="skills", criterion_class="flexible",
                          cd_reason="detail_missing", supporting_evidence=["Office tools"]))
_INVALID = _raw(_item(criterion_text="MS Office", status="UNCLEAR"))


class TestRepairCall:

    @pytest.mark.asyncio
    async def test_valid_first_response_makes_one_call(self):
        client = _client(_VALID)
        result = await _assess(client)
        assert client.chat.completions.create.await_count == 1
        assert result.matched_count == 1

    @pytest.mark.asyncio
    async def test_repair_success(self):
        client = _client(_INVALID, _VALID_CD)
        result = await _assess(client, prompt={"prompt_code": "recruitment.criteria_mapping",
                                               "version": 10, "system_prompt": "sys", "max_tokens": 7000})
        calls = client.chat.completions.create.await_args_list
        assert len(calls) == 2
        repair = calls[1].kwargs
        assert repair["max_tokens"] == 8000                       # max(7000, 8000)
        note = repair["messages"][-1]
        assert note["role"] == "user" and "status 'UNCLEAR'" in note["content"]
        assert repair["messages"][:2] == calls[0].kwargs["messages"]
        assert repair["messages"][2] == {"role": "assistant", "content": _INVALID}   # previous reply
        assert result.cannot_determine_count == 1
        assert result.assessments[0].cd_reason == "detail_missing"

    @pytest.mark.asyncio
    async def test_repair_keeps_higher_configured_max_tokens(self):
        client = _client("{bad json", _VALID)
        await _assess(client, prompt={"system_prompt": "sys", "max_tokens": 9000})
        assert client.chat.completions.create.await_args_list[1].kwargs["max_tokens"] == 9000

    @pytest.mark.asyncio
    async def test_repair_failure_raises_after_exactly_two_calls(self):
        client = _client(_INVALID, _INVALID, _VALID)   # a third response must never be requested
        with pytest.raises(CriteriaMappingResponseError, match="after repair call"):
            await _assess(client)
        assert client.chat.completions.create.await_count == 2

    @pytest.mark.asyncio
    async def test_invalid_json_then_invalid_json_raises(self):
        client = _client("{bad", "{still bad")
        with pytest.raises(CriteriaMappingResponseError, match="invalid JSON"):
            await _assess(client)
        assert client.chat.completions.create.await_count == 2

    @pytest.mark.asyncio
    async def test_summary_call_only_after_valid_response(self):
        summary = AsyncMock(return_value=None)
        with pytest.raises(CriteriaMappingResponseError):
            await _assess(_client(_INVALID, _INVALID), summary=summary)
        summary.assert_not_called()
        await _assess(_client(_INVALID, _VALID), summary=summary)
        summary.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_max_d01_calls_across_celery_attempts(self):
        """Always-invalid output: 2 D-01 calls per attempt x 4 attempts = 8 (unchanged ceiling)."""
        total = 0
        for _attempt in range(4):   # Celery max_retries=3 → 4 attempts
            client = _client(_INVALID, _INVALID, _INVALID)
            with pytest.raises(CriteriaMappingResponseError):
                await _assess(client)
            total += client.chat.completions.create.await_count
        assert total == 8


class TestMapperClientLimits:

    def test_client_built_with_one_retry_and_120s_timeout(self):
        fake_openai = types.ModuleType("openai")
        fake_openai.AsyncOpenAI = MagicMock(name="AsyncOpenAI")
        fake_config = types.ModuleType("config")
        fake_config.get_settings = lambda: types.SimpleNamespace(openai_api_key="sk-test")
        saved = mapper_mod._mapper_client
        mapper_mod._mapper_client = None
        try:
            with patch.dict(sys.modules, {"openai": fake_openai, "config": fake_config}):
                mapper_mod._get_mapper_client()
            fake_openai.AsyncOpenAI.assert_called_once_with(
                api_key="sk-test", max_retries=1, timeout=120.0)
        finally:
            mapper_mod._mapper_client = saved

    def test_limits_scoped_to_mapper_module(self):
        """Only llm_criteria_mapper's own client gets the D-01 limits."""
        hits = [p.relative_to(BACKEND).as_posix()
                for p in (BACKEND / "services").glob("*.py")
                if "_MAPPER_MAX_RETRIES" in p.read_text(encoding="utf-8")]
        assert hits == ["services/llm_criteria_mapper.py"]


# ═════════════════════════════════════════════════════════════════════════════
# 3. F-01 verified / upper scoring
# ═════════════════════════════════════════════════════════════════════════════

W = {"weight_skills": 40, "weight_experience": 30, "weight_education": 20, "weight_soft_skills": 10}
EXP = "Minimum 3 years of relevant HR experience"
CRIT = [
    ("Excel", "skills", True), ("SAP", "skills", True), ("Payroll", "skills", True),
    ("Power BI", "skills", False), (EXP, "experience", True),
    ("Bachelor in HR/Business", "education", True),
    ("Communication", "soft_skills", True), ("Arabic/English bilingual", "soft_skills", False),
]


def _llm(statuses: dict, match_types: dict | None = None, crit=CRIT) -> LLMMatchResult:
    match_types = match_types or {}
    out = []
    for text, dim, req in crit:
        st = statuses.get(text, "MATCHED")
        out.append(LLMCriterionAssessment(
            criterion_text=text, dimension=dim, required=req, status=st, confidence=0.8,
            supporting_evidence=[] if st == "ABSENT" else ["quoted"],
            match_reason="reason",
            match_type="missing" if st == "ABSENT" else match_types.get(text, "direct"),
            criterion_class="other",
            cd_reason="relevance_unverified" if st == CD else None,
        ))
    return LLMMatchResult(application_id="a", job_id="j", assessments=out, processing_ms=0,
                          created_at="", prompt_code="", prompt_version="", model="")


def _score(statuses, match_types=None, cfg=None, local=None, weights=W, crit=CRIT):
    engine = DeterministicScoringEngine(cfg or DeterministicScoringConfig())
    return deterministic_score_to_dict(engine.score(_llm(statuses, match_types, crit), weights, local))


GOLDEN = {
    # name: (statuses, match_types, verified, upper, pending, D_L, D_U, recommendation)
    "X": ({}, {"SAP": "equivalent"}, 100, 100, 0, "qualified", "qualified", "qualified"),
    "A": ({"Power BI": CD}, None, 88, 100, 12, "qualified", "qualified", "qualified"),
    "Y": ({"Power BI": "ABSENT", "Arabic/English bilingual": "ABSENT"},
          {"Payroll": "transferable", "SAP": "inferred"}, 80, 80, 0, "qualified", "qualified", "qualified"),
    "B": ({EXP: CD, "Arabic/English bilingual": "PARTIAL"}, None, 69, 99, 30,
          "partial", "qualified", "needs_verification"),
    "C": ({EXP: CD, "Communication": CD, "Power BI": CD, "SAP": "PARTIAL",
           "Arabic/English bilingual": "ABSENT"}, None, 44, 93, 49,
          "partial", "qualified", "needs_verification"),
    "Z": ({"Payroll": "ABSENT"}, None, 91, 91, 0, "partial", "partial", "partial"),
    "D": ({"Payroll": "ABSENT", "SAP": "ABSENT", EXP: CD}, None, 52, 82, 30,
          "partial", "partial", "partial"),
    "E": ({"Excel": "ABSENT", "SAP": "ABSENT", "Payroll": "ABSENT", EXP: CD, "Communication": CD,
           "Power BI": "ABSENT"}, None, 23, 60, 37, "rejected", "partial", "partial"),
}


class TestGoldenCandidates:

    @pytest.fixture(autouse=True)
    def _no_embedding_model(self):
        # Candidate Y's inferred SAP runs the evidence-overlap check; keep it
        # deterministic and offline (the evidence "quoted" never overlaps).
        with patch("services.deterministic_scoring._compute_semantic_similarity", return_value=0.0):
            yield

    @pytest.mark.parametrize("name", list(GOLDEN))
    def test_golden(self, name):
        statuses, mts, verified, upper, pending, d_l, d_u, rec = GOLDEN[name]
        d = _score(statuses, mts)
        assert (d["verified_score"], d["upper_score"], d["pending_points"]) == (verified, upper, pending)
        assert d["final_score"] == verified
        assert (d["decision_verified_basis"], d["decision_if_verified"], d["recommendation"]) == (d_l, d_u, rec)

    def test_candidate_e_not_rejected(self):
        d = _score(GOLDEN["E"][0])
        assert d["decision_verified_basis"] == "rejected"
        assert d["recommendation"] != "rejected"


class TestVerifiedUpperCredits:

    def _crit(self, d, text):
        return next(c for dim in d["dimensions"].values() for c in dim["criteria"] if c["criterion_text"] == text)

    def test_credits_per_status(self):
        d = _score({"SAP": "PARTIAL", "Payroll": "ABSENT", "Excel": CD})
        assert (self._crit(d, "Power BI")["verified_credit"], self._crit(d, "Power BI")["upper_credit"]) == (1.0, 1.0)
        assert (self._crit(d, "SAP")["verified_credit"], self._crit(d, "SAP")["upper_credit"]) == (0.5, 0.5)
        assert (self._crit(d, "Payroll")["verified_credit"], self._crit(d, "Payroll")["upper_credit"]) == (0.0, 0.0)
        excel = self._crit(d, "Excel")
        assert (excel["verified_credit"], excel["upper_credit"]) == (0.0, 1.0)
        assert excel["effective_credit"] == excel["verified_credit"]
        assert excel["cd_reason"] == "relevance_unverified"
        assert excel["match_reason"] == "reason"

    def test_cd_skips_status_specific_adjustments(self):
        d = _score({"Excel": CD}, {"Excel": "missing"})
        excel = self._crit(d, "Excel")
        assert excel["match_type"] == "missing"                  # no inferred normalisation
        assert "inferred_from_evidence" not in excel["risk_flags"]
        assert excel["upper_credit"] == 1.0                       # full credit regardless of qf

    def test_cd_stays_in_denominator(self):
        # skills: 3 required (Excel CD) + 1 preferred → req verified avg 2/3
        d = _score({"Excel": CD})
        skills = d["dimensions"]["skills"]
        assert skills["required_avg"] == pytest.approx(2 / 3, abs=1e-4)
        assert skills["dimension_score_upper"] == pytest.approx(1.0)
        assert skills["n_required_cannot_determine"] == 1

    def test_pending_worth_and_multiple_cds(self):
        d = _score(GOLDEN["C"][0])
        worth = {i["criterion_text"]: i["pending_worth"] for i in d["verification_items"]}
        assert worth == {EXP: 30.0, "Communication": 7.0, "Power BI": 12.0}
        # required items first, then by worth
        assert [i["required"] for i in d["verification_items"]] == [True, True, False]
        assert d["required_summary"]["cannot_determine"] == 2
        assert d["preferred_summary"]["cannot_determine"] == 1

    def test_required_cd_never_blocking(self):
        d = _score({EXP: CD, "Payroll": "ABSENT"})
        assert d["required_summary"]["blocking_gaps"] == 1       # Payroll only
        assert d["required_summary"]["absent"] == 1
        assert d["required_summary"]["fully_covered"] is False

    def test_absent_floor_counts_absent_only(self):
        cfg = DeterministicScoringConfig(enable_required_absent_floor=True,
                                         required_absent_floor_threshold=0.5,
                                         required_absent_floor_cap=0.4)
        # skills: Excel ABSENT, SAP CD, Payroll CD → 1/3 absent: floor NOT triggered
        d = _score({"Excel": "ABSENT", "SAP": CD, "Payroll": CD}, cfg=cfg)
        assert d["dimensions"]["skills"]["required_absent_floor_triggered"] is False
        # 2/3 absent → floor applies; the upper (0.7/3 + 0.3 = 0.533) is capped at 0.4
        d = _score({"Excel": "ABSENT", "SAP": "ABSENT", "Payroll": CD}, cfg=cfg)
        sk = d["dimensions"]["skills"]
        assert sk["dimension_score"] == pytest.approx(0.3)      # below the cap already
        assert sk["dimension_score_upper"] == pytest.approx(0.4)

    def test_preferred_cd_shown_but_never_changes_decision(self):
        d = _score({"Power BI": CD, "Payroll": "ABSENT"})
        assert d["pending_points"] > 0
        assert d["decision_verified_basis"] == d["decision_if_verified"] == d["recommendation"]
        assert d["required_summary"]["cannot_determine"] == 0

    @pytest.mark.parametrize("statuses", [
        {}, {"Payroll": "ABSENT"}, {"SAP": "PARTIAL", "Communication": "ABSENT"},
        {"Excel": "ABSENT", "SAP": "ABSENT", "Payroll": "ABSENT", EXP: "ABSENT"},
    ])
    def test_no_cd_regression_identical_to_signal_decision(self, statuses):
        d = _score(statuses)
        assert d["upper_score"] == d["verified_score"] == d["final_score"]
        assert d["pending_points"] == 0 and d["verification_items"] == []
        assert d["recommendation"] == decision_from_signal(d["recruiter_signal"])

    def test_schema_v3(self):
        d = _score({})
        assert d["_schema"] == "det_score_v3"


class TestRecommendationRule:

    @pytest.mark.parametrize("n_cd, d_l, d_u, expected", [
        (0, "partial", "qualified", "partial"),                 # no required CD → D_L
        (0, "rejected", "rejected", "rejected"),
        (1, "partial", "qualified", "needs_verification"),      # verification can qualify
        (1, "rejected", "qualified", "needs_verification"),
        (2, "rejected", "partial", "partial"),                  # never reject because of CD
        (1, "partial", "partial", "partial"),                   # D_L == D_U
        (1, "qualified", "qualified", "qualified"),
        (1, "rejected", "rejected", "rejected"),                # confirmed gaps alone reject
    ])
    def test_branches(self, n_cd, d_l, d_u, expected):
        assert recommendation_from_decisions(n_cd, d_l, d_u) == expected


class TestPhase3LocalRelevanceCondition:

    def _local(self, status, reason, evidence):
        return [{"criterion_text": "Minimum 7 years of relevant experience", "status": status,
                 "confidence": 0.45, "partial_reason": reason, "dimension": "experience",
                 "required": True, "supporting_evidence": evidence}]

    def _run(self, llm_evidence, local):
        crit = [("Minimum 7 years of relevant experience", "experience", True)]
        llm = _llm({}, crit=crit)
        llm.assessments[0].supporting_evidence = llm_evidence
        engine = DeterministicScoringEngine()
        d = deterministic_score_to_dict(engine.score(llm, {"weight_experience": 100}, local))
        return d["dimensions"]["experience"]["criteria"][0]

    def test_relevance_only_disagreement_becomes_cd(self):
        c = self._run(["Total Experience: 8.0 years", "Senior HR Officer, ACME Ltd (2017 - 2024)"],
                      self._local("PARTIAL", "8 years total experience, but relevance to role not verified",
                                  ["8.0 years total experience extracted from CV"]))
        assert c["status"] == CD and c["cd_reason"] == "relevance_unverified"
        assert "local_relevance_check" in c["risk_flags"]

    def test_established_years_shortfall_stays_partial(self):
        c = self._run(["Total Experience: 3.0 years"],
                      self._local("PARTIAL", "3 years total experience, but relevance to role not verified",
                                  ["3.0 years total experience extracted from CV"]))
        assert c["status"] == "PARTIAL"
        assert "local_relevance_check" not in c["risk_flags"]

    def test_local_numeric_comparison_failed_stays_absent(self):
        c = self._run(["Total Experience: 8.0 years"],
                      self._local("ABSENT", "No verifiable years and no clear role relevance found in CV", []))
        assert c["status"] == "ABSENT"


# ═════════════════════════════════════════════════════════════════════════════
# 4. Serialisation and historical compatibility
# ═════════════════════════════════════════════════════════════════════════════

class TestSerialisation:

    def test_cd_reason_round_trip(self):
        llm = _llm({EXP: CD})
        llm.cannot_determine_count = 1
        back = llm_matchresult_from_dict(json.loads(json.dumps(llm_matchresult_to_dict(llm))))
        exp = next(a for a in back.assessments if a.criterion_text == EXP)
        assert exp.status == CD and exp.cd_reason == "relevance_unverified"
        assert back.cannot_determine_count == 1

    def test_historical_llm_json_without_cd_reason_loads(self):
        old = {"application_id": "a", "job_id": "j", "assessments": [
            {"criterion_text": "Python", "dimension": "skills", "required": True, "status": "MATCHED",
             "confidence": 0.9, "supporting_evidence": ["x"], "match_reason": "r",
             "match_type": "direct", "criterion_class": "strict", "risk_flags": []}]}
        r = llm_matchresult_from_dict(old)
        assert r.assessments[0].cd_reason is None and r.cannot_determine_count == 0

    def test_missing_skills_excludes_cd(self):
        d = _score({"Excel": CD, "SAP": "ABSENT"})
        flat = extract_flat_columns_from_det_score_json(json.dumps(d))
        assert flat["missing_skills"] == ["SAP"]
        assert "Excel" not in flat["matched_skills"]

    @pytest.mark.parametrize("payload", [
        None,
        {"_schema": "det_score_v2", "final_score": 72, "required_summary": {"total": 3}},
        {"_schema": "det_score_v1", "final_score": 72},
        "not a dict",
    ])
    def test_historical_payloads_read_as_fully_verified(self, payload):
        v = verification_summary(payload, 72)
        assert (v["verified_score"], v["score_upper"], v["pending_points"]) == (72, 72, 0)
        assert v["required_to_verify"] == 0 and v["preferred_to_verify"] == 0
        assert v["decision_if_verified"] is None

    def test_v3_payload_fields(self):
        d = _score(GOLDEN["B"][0])
        v = verification_summary(d, d["final_score"])
        assert (v["verified_score"], v["score_upper"], v["pending_points"]) == (69, 99, 30)
        assert v["required_to_verify"] == 1 and v["decision_if_verified"] == "qualified"

    def test_unscored_has_no_upper(self):
        assert verification_summary(None, None)["score_upper"] is None

    def test_scoring_method_constants(self):
        assert DETERMINISTIC == "deterministic_v1"      # historical value unchanged
        assert DETERMINISTIC_V2 == "deterministic_v2"
        assert re.match(r"^[a-z][a-z0-9_]*_v[0-9]+$", DETERMINISTIC_V2)


# ═════════════════════════════════════════════════════════════════════════════
# 5. Ordering, export, migration 105
# ═════════════════════════════════════════════════════════════════════════════

class TestCandidateOrdering:

    def _parse(self, order_by):
        pglast = pytest.importorskip("pglast")
        return pglast.parse_sql(
            "SELECT 1 FROM applications a LEFT JOIN application_scores s "
            f"ON s.application_id = a.application_id ORDER BY {order_by}")

    def test_default_is_newest_first_with_tiebreaker(self):
        assert candidate_order_by("applied_at", "desc") == "a.applied_at DESC, a.application_id"
        assert candidate_order_by("unknown", "desc") == "a.applied_at DESC, a.application_id"

    @pytest.mark.parametrize("key", sorted(CANDIDATE_SORT_FIELDS))
    @pytest.mark.parametrize("order", ["desc", "asc"])
    def test_every_ordering_is_valid_sql_and_ends_with_application_id(self, key, order):
        ob = candidate_order_by(key, order)
        assert ob.endswith("a.application_id")
        self._parse(ob)

    def test_score_order(self):
        ob = candidate_order_by("score", "desc")
        parts = [p.strip() for p in re.split(r",\s(?![^()]*\))", ob)]
        assert parts[0] == "COALESCE(s.det_final_score, s.final_score) DESC NULLS LAST"
        assert "required_summary" in parts[1] and parts[1].endswith("ASC")
        assert "pending_points" in parts[2] and parts[2].endswith("ASC")
        assert parts[3:] == ["a.applied_at DESC", "a.application_id"]

    def test_upper_and_pending_orders_put_unscored_last(self):
        assert re.match(r"^\(COALESCE\(s\.det_final_score, s\.final_score\) \+ .*\) ASC NULLS LAST, ",
                        candidate_order_by("score_upper", "asc"))
        assert candidate_order_by("pending_points", "desc").startswith(
            "(CASE WHEN COALESCE(s.det_final_score, s.final_score) IS NULL THEN NULL")

    def test_router_uses_shared_ordering_everywhere(self):
        src = (BACKEND / "routers" / "applications.py").read_text(encoding="utf-8")
        assert src.count("ORDER BY {order_by}") == 2
        assert "sort_column" not in src
        assert "ORDER BY a.applied_at DESC, a.application_id" in src
        camp = (BACKEND / "routers" / "campaigns.py").read_text(encoding="utf-8")
        assert "ORDER BY a.applied_at DESC, a.application_id" in camp


def _router_export_namespace() -> dict:
    """Compile only the export helpers from routers/applications.py (the router
    itself can't be imported in this environment)."""
    src = (BACKEND / "routers" / "applications.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    wanted = {"_EXPORT_COLUMNS", "_AI_RECOMMENDATION_LABELS", "_AI_DECISION_FILTER_LABELS",
              "_effective_workflow_status", "_ai_recommendation_label", "_export_row"}
    body = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            body.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            if getattr(target, "id", None) in wanted:
                body.append(node)
    ns: dict = {}
    exec(compile(ast.Module(body=body, type_ignores=[]), "applications_export", "exec"), ns)
    return ns


_P001_COLUMNS = [
    "Candidate Name", "Candidate Email", "Job Title", "Job Code", "Campaign", "Client",
    "AI Score", "AI Recommendation", "Workflow Status", "Assigned Recruiter", "Applied Date",
    "Security Status", "Duplicate Status", "Talent Pool", "Recruiter Notes", "Scoring Method",
]


class TestExport:

    def test_columns_appended_last_existing_unchanged(self):
        cols = _router_export_namespace()["_EXPORT_COLUMNS"]
        assert cols[:16] == _P001_COLUMNS
        assert cols[16:] == ["Pending Verification Points", "Score Range Upper", "Required Criteria To Verify"]

    def _row(self, **over):
        from datetime import datetime
        r = {"candidate_name": "N", "candidate_email": "e", "job_title": "T", "job_code": "J",
             "campaign_name": "", "client_org_name": "", "score": 69, "status": "needs_verification",
             "processing_status": "ai_scored", "workflow_status": None, "assigned_user_name": None,
             "applied_at": datetime(2026, 9, 1, 10, 0), "security_check_status": "passed",
             "duplicate_status": None, "is_talent_pool": False, "recruiter_notes": None,
             "scoring_method": "deterministic_v2", "pending_points": 30, "score_upper": 99,
             "required_to_verify": 1}
        r.update(over)
        return r

    def test_row_values(self):
        ns = _router_export_namespace()
        row = ns["_export_row"](self._row())
        assert len(row) == len(ns["_EXPORT_COLUMNS"])
        assert row[6] == 69.0                        # AI Score = verified score
        assert row[7] == "Needs Verification"
        assert row[-3:] == [30.0, 99.0, 1]

    def test_unscored_row_blank_verification_cells(self):
        ns = _router_export_namespace()
        row = ns["_export_row"](self._row(score=None, status=None, pending_points=None,
                                          score_upper=None, required_to_verify=0))
        assert row[7] == "Not Scored"
        assert row[-3:] == ["", "", ""]

    def test_filter_label(self):
        assert _router_export_namespace()["_AI_DECISION_FILTER_LABELS"]["needs_verification"] == "Needs Verification"


class TestMigration105:
    SQL = (BACKEND / "db" / "migrations" / "105_needs_verification.sql").read_text(encoding="utf-8")

    def test_idempotent_shape(self):
        body = "\n".join(l for l in self.SQL.splitlines() if not l.lstrip().startswith("--"))
        drop = body.index("DROP CONSTRAINT IF EXISTS applications_decision_check")
        add = body.index("ADD CONSTRAINT applications_decision_check")
        assert drop < add
        assert "BEGIN;" in body and "COMMIT;" in body and "SET search_path = cv_analyzer;" in body
        for forbidden in ("UPDATE ", "INSERT ", "DELETE ", "ADD COLUMN"):
            assert forbidden not in body

    def test_values(self):
        m = re.search(r"CHECK \(decision IN \(([^)]*)\)\)", self.SQL)
        assert {v.strip().strip("'") for v in m.group(1).split(",")} == {
            "qualified", "partial", "rejected", "needs_verification"}

    def test_parses(self):
        pglast = pytest.importorskip("pglast")
        body = "\n".join(l for l in self.SQL.splitlines() if not l.lstrip().startswith("--"))
        assert len(pglast.parse_sql(body)) == 5


# ═════════════════════════════════════════════════════════════════════════════
# 6. v10 prompt builder (offline)
# ═════════════════════════════════════════════════════════════════════════════

_V9_LIKE = """\
You are an expert CV-to-job-criteria mapping analyst.

For TYPE B criteria: do NOT assign MATCHED/direct/confidence≥0.85 based on
total years alone. If years are met AND you have title/responsibility/
domain evidence confirming relevance → MATCHED is appropriate. If years
are met but you have NO title/responsibility/domain evidence to confirm
relevance (e.g. you were only given a total-years figure with no itemized
roles) → assign PARTIAL, match_type="inferred", confidence 0.35-0.59, state
in match_reason that years are met but relevance is unconfirmed. If years
are NOT met → PARTIAL.

REQUIRED vs PREFERRED:
- required=true criteria are hard requirements. Missing evidence → ABSENT. Partial evidence → PARTIAL.
- required=false criteria are nice-to-have. Apply flexible judgment; missing is normal.

MATCH STATUS DEFINITIONS:
- MATCHED:  Clear, sufficient evidence in CV that the criterion is met.
- PARTIAL:  Some evidence exists but it is incomplete.
- ABSENT:   No evidence found in the CV.

IMPORTANT: Only set status=MATCHED or status=PARTIAL if you can provide at least one entry in supporting_evidence. If you cannot cite any supporting text, set status=ABSENT.

MATCH TYPE GUIDE:
- direct: explicit.

RISK FLAGS:
- relevance_unverified:   years met, relevance unconfirmed (status PARTIAL).

OUTPUT:
{
  "assessments": [
    {
      "status": "<MATCHED|PARTIAL|ABSENT>",
      "risk_flags": []
    }
  ],
  "qualitative_summary": {
    "gaps_identified": ["<gap derived from ABSENT/PARTIAL assessments above>"]
  }
}

QUALITATIVE SUMMARY RULES:
QS3. strengths and gaps reference criteria.
QS4. suggested_interview_questions should target PARTIAL or ABSENT criteria areas.
QS5. Keep it short.
"""


def _builder():
    sys.path.insert(0, str(BACKEND / "scripts"))
    import build_d01_prompt_v10 as b
    return b


class TestPromptV10Builder:

    def test_all_edits_applied_and_rest_untouched(self):
        r = _builder().build_v10(_V9_LIKE)
        assert r.errors == [] and r.not_found == [] and r.review == []
        t = r.text
        assert "ASSESSMENT STATUS CONTRACT" in t and "MATCH STATUS DEFINITIONS" not in t
        assert '"status": "<MATCHED|PARTIAL|ABSENT|CANNOT_DETERMINE>",' in t
        assert '"cd_reason":' in t
        assert 'assign ABSENT (a total-years figure alone does not establish relevance), state' in t
        assert "are NOT met → PARTIAL." in t                    # known shortfall untouched
        assert "Partial evidence → PARTIAL" not in t
        assert "(status CANNOT_DETERMINE)" in t
        assert "(never CANNOT_DETERMINE)" in t
        assert t.count("QS4.") == 1 and "verify CANNOT_DETERMINE criteria" in t and "QS5. Keep it short." in t
        for keep in ("You are an expert CV-to-job-criteria mapping analyst.", "MATCH TYPE GUIDE:",
                     "- direct: explicit.", "QS3. strengths and gaps reference criteria."):
            assert keep in t

    def test_rerun_on_v10_refused(self):
        b = _builder()
        assert b.build_v10(b.build_v10(_V9_LIKE).text).errors

    def test_missing_required_anchor_writes_nothing(self, tmp_path):
        b = _builder()
        src = tmp_path / "v9.txt"
        src.write_text("no anchors here", encoding="utf-8")
        assert b.main(["--v9-file", str(src), "--out-dir", str(tmp_path / "out")]) == 2
        assert not (tmp_path / "out" / "d01_prompt_v10.txt").exists()
        assert (tmp_path / "out" / "d01_prompt_v10_report.md").exists()

    def test_cli_writes_v10_diff_report(self, tmp_path):
        b = _builder()
        src = tmp_path / "v9.txt"
        src.write_text(_V9_LIKE, encoding="utf-8")
        assert b.main(["--v9-file", str(src), "--out-dir", str(tmp_path)]) == 0
        assert "ASSESSMENT STATUS CONTRACT" in (tmp_path / "d01_prompt_v10.txt").read_text(encoding="utf-8")
        assert (tmp_path / "d01_prompt_v9_to_v10.diff").read_text(encoding="utf-8").startswith("--- v9")

    def test_contract_text_is_the_code_fallback_contract(self):
        assert mapper_mod.D01_STATUS_CONTRACT.rstrip("\n") in _builder().build_v10(_V9_LIKE).text
        assert mapper_mod.D01_STATUS_CONTRACT in mapper_mod._HARDCODED_SYSTEM_PROMPT
        assert "{D01_STATUS_CONTRACT}" not in mapper_mod._HARDCODED_SYSTEM_PROMPT


class TestPromptV10BuilderProductionV9Wording:
    """Wording taken from production v9 (md5 18de95b0…): banner-style headings
    and the 'years threshold is met/exceeded … Assign status=PARTIAL' sentence."""

    V9_SNIPPET = """\
For Type B criteria:
- If the years threshold is met/exceeded but you have NO title, responsibility,
  or domain evidence available to confirm relevance (for example, the
  experience data you were given contains only a total-years figure with no
  itemized roles) → do not guess relevance in either direction. Assign
  status=PARTIAL, match_type="inferred", confidence in the 0.35–0.59 range,
  and state plainly in match_reason that the years threshold is met but
  relevance could not be confirmed from the available data. Add risk_flag
  "relevance_unverified".

=====================================================================
MATCH STATUS DEFINITIONS
=====================================================================
- MATCHED:  Clear, sufficient evidence that the criterion is met.
- PARTIAL:  Some evidence exists but it is incomplete.
- ABSENT:   No evidence found anywhere in the provided data.

IMPORTANT: Only set status=MATCHED or status=PARTIAL if you can provide at least one entry in supporting_evidence.

=====================================================================
MATCH TYPE GUIDE
=====================================================================
- direct: explicit.
      "status": "<MATCHED|PARTIAL|ABSENT>",
"""

    def test_v9_type_b_and_banner(self):
        r = _builder().build_v10(self.V9_SNIPPET)
        assert r.errors == []
        t = r.text
        assert 'Assign\n  status=ABSENT (a total-years figure alone does not establish relevance),\n  and state plainly' in t
        # banner kept directly under the new heading; old definitions gone
        assert "=====\nASSESSMENT STATUS CONTRACT (authoritative" in t
        assert ("(authoritative for choosing the status; the evidence-interpretation principles elsewhere "
                "in these instructions still decide what the CV demonstrates):\n=====") in t
        assert "MATCH STATUS DEFINITIONS" not in t and "- PARTIAL:  Some evidence exists" not in t
        # E7: the v9 evidence paragraph also covers CANNOT_DETERMINE
        assert ("IMPORTANT: Only set status=MATCHED, status=PARTIAL or status=CANNOT_DETERMINE "
                "if you can provide at least one entry in supporting_evidence.") in t
        assert any(a.startswith("E7 ") for a in r.applied)


# ═════════════════════════════════════════════════════════════════════════════
# 7. Offline v9/v10 comparison — max_tokens safeguards
# ═════════════════════════════════════════════════════════════════════════════

def _compare_mod():
    sys.path.insert(0, str(BACKEND / "scripts"))
    import p002a_offline_compare as c
    return c


_V9_ROW = {"system_prompt": "v9 prompt", "model": "gpt-4o-mini", "temperature": 0.1,
           "max_tokens": 7000, "output_language": "en"}


class TestOfflineCompareMaxTokens:

    def test_default_is_12000(self):
        c = _compare_mod()
        assert c.V10_MAX_TOKENS == 12000
        with patch.object(c, "main_async", AsyncMock(return_value=0)) as run:
            c.main(["--out-dir", "/tmp/x", "--dry-run"])
        assert run.await_args.args[0].v10_max_tokens == 12000

    def test_v9_keeps_production_settings_v10_uses_12000(self):
        v9, v10 = _compare_mod().build_prompt_configs(_V9_ROW, "v10 prompt", 12000)
        assert (v9["version"], v9["max_tokens"], v9["system_prompt"]) == (9, 7000, "v9 prompt")
        assert (v10["version"], v10["max_tokens"], v10["system_prompt"]) == (10, 12000, "v10 prompt")
        for k in ("model", "temperature", "output_language", "prompt_code"):
            assert v9[k] == v10[k]

    @pytest.mark.parametrize("bad", [0, 7000, 8000, 16000])
    def test_refuses_any_other_v10_value(self, bad):
        with pytest.raises(SystemExit, match="expected 12000"):
            _compare_mod().build_prompt_configs(_V9_ROW, "v10", bad)

    def test_expected_call_tokens(self):
        c = _compare_mod()
        v9, v10 = c.build_prompt_configs(_V9_ROW, "v10", 12000)
        assert c.expected_call_tokens(v9) == (7000, 8000)      # repair = max(7000, 8000)
        assert c.expected_call_tokens(v10) == (12000, 12000)   # repair = max(12000, 8000)

    def test_check_call_tokens(self):
        c = _compare_mod()
        _, v10 = c.build_prompt_configs(_V9_ROW, "v10", 12000)
        c.check_call_tokens(v10, [12000])
        c.check_call_tokens(v10, [12000, 12000])
        with pytest.raises(SystemExit, match="expected"):
            c.check_call_tokens(v10, [7000])
        with pytest.raises(SystemExit, match="expected"):
            c.check_call_tokens(v10, [12000, 8000])

    @pytest.mark.asyncio
    async def test_run_one_really_sends_12000_on_main_and_repair(self):
        c = _compare_mod()
        v9, v10 = c.build_prompt_configs(_V9_ROW, "v10 prompt", 12000)
        from services.cv_evidence import CVFacts
        case = {"text": "MS Office user. " * 5, "analysis_json": ANALYSIS_JSON,
                "weights": {"weight_skills": 100}}
        facts = CVFacts(language="en", total_char_count=10)

        client = _client(_INVALID, _VALID_CD)
        with patch.object(mapper_mod, "_get_mapper_client", return_value=client), c.mapper_hooks():
            r = await c.run_one(v10, case, "app-1", "job-1", DeterministicScoringConfig(), facts, None)
        assert r["ok"] and r["repair_used"]
        assert r["max_tokens_sent"] == [12000, 12000]
        assert [k.kwargs["max_tokens"] for k in client.chat.completions.create.await_args_list] == [12000, 12000]

        client = _client(_INVALID, _VALID)
        with patch.object(mapper_mod, "_get_mapper_client", return_value=client), c.mapper_hooks():
            r = await c.run_one(v9, case, "app-1", "job-1", DeterministicScoringConfig(), facts, None)
        assert r["max_tokens_sent"] == [7000, 8000]              # v9 unchanged from production

    @pytest.mark.asyncio
    async def test_run_one_aborts_if_mapper_sends_wrong_tokens(self):
        c = _compare_mod()
        _, v10 = c.build_prompt_configs(_V9_ROW, "v10 prompt", 12000)
        from services.cv_evidence import CVFacts
        case = {"text": "MS Office user. " * 5, "analysis_json": ANALYSIS_JSON, "weights": {"weight_skills": 100}}
        wrong = dict(v10, max_tokens=7000)   # simulate a config that slipped past build_prompt_configs
        with patch.object(mapper_mod, "_get_mapper_client", return_value=_client(_VALID)), \
             patch.object(c, "expected_call_tokens", return_value=(12000, 12000)), c.mapper_hooks():
            with pytest.raises(SystemExit, match=r"sent \[7000\]"):
                await c.run_one(wrong, case, "a", "j", DeterministicScoringConfig(),
                                CVFacts(language="en", total_char_count=10), None)


# ═════════════════════════════════════════════════════════════════════════════
# 8. Offline comparison — per-call capture and report
# ═════════════════════════════════════════════════════════════════════════════

def _resp(content, finish="stop", prompt_tokens=5000, completion_tokens=900):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content), finish_reason=finish)],
        usage=types.SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens))


def _seq_client(*items):
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=list(items))
    return client


class TestOfflineCompareCapture:

    def _setup(self):
        c = _compare_mod()
        v9, v10 = c.build_prompt_configs(_V9_ROW, "v10 prompt", 12000)
        from services.cv_evidence import CVFacts
        case = {"text": "MS Office user. " * 5, "analysis_json": ANALYSIS_JSON, "weights": {"weight_skills": 100}}
        return c, v9, v10, case, CVFacts(language="en", total_char_count=10)

    @pytest.mark.asyncio
    async def test_call_log_truncated_main_then_repair(self):
        c, _, v10, case, facts = self._setup()
        client = _seq_client(_resp('{"assessments": [{"crit', finish="length", completion_tokens=12000),
                             _resp(_VALID_CD, completion_tokens=800))
        with patch.object(mapper_mod, "_get_mapper_client", return_value=client), c.mapper_hooks():
            r = await c.run_one(v10, case, "a1", "j1", DeterministicScoringConfig(), facts, None)
        main, repair = r["call_log"]
        assert (main["call"], main["max_tokens"], main["finish_reason"]) == ("main", 12000, "length")
        assert main["completion_tokens"] == 12000 and main["validation_errors"] == ["invalid JSON"]
        assert (repair["call"], repair["max_tokens"], repair["finish_reason"]) == ("repair", 12000, "stop")
        assert repair["validation_errors"] == [] and repair["prompt_tokens"] == 5000
        cd = r["assessments"][0]
        assert cd["final_status"] == CD and cd["supporting_evidence"] == ["Office tools"]
        assert cd["final_cd_reason"] == "detail_missing" and cd["pending_worth"] > 0

    @pytest.mark.asyncio
    async def test_call_log_records_openai_exception(self):
        c, v9, _, case, facts = self._setup()
        client = _seq_client(TimeoutError("Request timed out."))
        with patch.object(mapper_mod, "_get_mapper_client", return_value=client), c.mapper_hooks():
            r = await c.run_one(v9, case, "a1", "j1", DeterministicScoringConfig(), facts, None)
        assert r["ok"] is False and "TimeoutError" in r["error"]
        (call,) = r["call_log"]
        assert call["call"] == "main" and "TimeoutError" in call["error"]

    @pytest.mark.asyncio
    async def test_capture_does_not_change_the_run(self):
        """Same responses → same assessments with and without the capture wrapper."""
        c, v9, _, case, facts = self._setup()
        with patch.object(mapper_mod, "_get_mapper_client", return_value=_client(_INVALID, _VALID)), c.mapper_hooks():
            r = await c.run_one(v9, case, "a1", "j1", DeterministicScoringConfig(), facts, None)
        direct = await _assess(_client(_INVALID, _VALID))
        assert [a["llm_status"] for a in r["assessments"]] == [a.status for a in direct.assessments]
        assert r["call_log"][0]["validation_errors"][0].startswith("assessment[0]")

    def _results(self):
        def run(v, aid, job, statuses, verified, upper, rec, calls):
            return {"application_id": aid, "job_code": job, "prompt_version": v, "ok": True,
                    "configured_max_tokens": 12000 if v == 10 else 7000, "calls": len(calls),
                    "repair_used": len(calls) > 1, "seconds": 3.0, "verified": verified, "upper": upper,
                    "pending": upper - verified, "recommendation": rec, "call_log": calls,
                    "assessments": [{"criterion_text": t, "dimension": "skills", "required": True,
                                     "final_status": st, "final_cd_reason": "detail_missing" if st == CD else None,
                                     "match_reason": "SECRET-MATCH-REASON", "pending_worth": 20.0 if st == CD else 0,
                                     "supporting_evidence": ["SECRET-CV-QUOTE"]} for t, st in statuses]}
        ok9 = [{"call": "main", "max_tokens": 7000, "finish_reason": "stop", "prompt_tokens": 4000,
                "completion_tokens": 700, "validation_errors": []}]
        ok10 = [{"call": "main", "max_tokens": 12000, "finish_reason": "length", "prompt_tokens": 4500,
                 "completion_tokens": 12000, "validation_errors": ["invalid JSON"]},
                {"call": "repair", "max_tokens": 12000, "finish_reason": "stop", "prompt_tokens": 4600,
                 "completion_tokens": 900, "validation_errors": []}]
        fail9 = {"application_id": "big-1", "job_code": "JOB-2026-0110", "prompt_version": 9, "ok": False,
                 "configured_max_tokens": 7000, "calls": 2, "seconds": 20.0,
                 "error": "validation: D-01 response invalid ... after repair call: invalid JSON",
                 "call_log": [dict(ok9[0], finish_reason="length", completion_tokens=7000,
                                   validation_errors=["invalid JSON"]),
                              {"call": "repair", "max_tokens": 8000, "finish_reason": "length",
                               "prompt_tokens": 4000, "completion_tokens": 8000, "validation_errors": ["invalid JSON"]}]}
        return [
            run(9, "a1", "JOB-1", [("Excel", "MATCHED"), ("SAP", "PARTIAL")], 75, 75, "partial", ok9),
            run(10, "a1", "JOB-1", [("Excel", "MATCHED"), ("SAP", CD)], 50, 100, "needs_verification", ok10),
            fail9,
            run(10, "big-1", "JOB-2026-0110", [("Excel", "ABSENT")], 0, 0, "rejected", ok10),
        ]

    def test_report_sections(self):
        c = _compare_mod()
        rep = c.build_report(self._results(), [{"job_code": "JOB-1"}, {"job_code": "JOB-2026-0110"}],
                             ["JOB-2026-0110"], details=True)
        for needle in ("## Prompt v9", "## Prompt v10", "truncated responses (finish_reason=length): 2",
                       "repair [8000]", "repair [12000]", "estimated cost $", "| all | ALL |",
                       "## v10 per application", "| PARTIAL | 0 | 0 | 0 | 1 |", "changed: 1 of 2",
                       "- partial → needs_verification: 1", "Material differences", "not comparable",
                       "## Every CANNOT_DETERMINE case", "SECRET-CV-QUOTE", "## Focus job JOB-2026-0110",
                       "FAILED RUN JOB-2026-0110 big-1"):
            assert needle in rep, needle

    def test_summary_has_no_cv_text(self):
        c = _compare_mod()
        s = c.build_report(self._results(), [], ["JOB-2026-0110"], details=False)
        assert "SECRET-CV-QUOTE" not in s and "SECRET-MATCH-REASON" not in s
        assert "## Focus job JOB-2026-0110" in s and "Material differences" in s

    def test_report_only_rebuilds_from_files(self, tmp_path):
        c = _compare_mod()
        with open(tmp_path / "results.jsonl", "w") as f:
            for r in self._results():
                f.write(json.dumps(r) + "\n")
        (tmp_path / "sample.json").write_text(json.dumps([{"job_code": "JOB-1"}]))
        assert c.main(["--out-dir", str(tmp_path), "--report-only", "--include-job", "JOB-2026-0110"]) == 0
        for name in ("report.md", "summary.txt", "cd_review.tsv"):
            assert (tmp_path / name).exists()
        assert "SECRET-CV-QUOTE" in (tmp_path / "cd_review.tsv").read_text()


# ═════════════════════════════════════════════════════════════════════════════
# 9. Offline comparison — isolation of concurrent runs (regression: v9 run
#    recorded a v10 call "[7000, 12000]" because per-run patches overlapped)
# ═════════════════════════════════════════════════════════════════════════════

class TestOfflineCompareConcurrency:

    def _slow_client(self, tag, log):
        """Fake OpenAI client: each call takes time (so tasks interleave), first
        response is invalid to force a repair call, second is valid."""
        state = {"n": 0}

        async def create(**kw):
            state["n"] += 1
            sysp = kw["messages"][0]["content"]
            log.append((tag, kw["max_tokens"],
                        "v10" if sysp.startswith("v10 prompt") else "v9" if sysp.startswith("v9 prompt") else "other"))
            await asyncio.sleep(0.02 if tag % 2 else 0.035)
            body = {"assessments": [dict(_item(criterion_text="MS Office", dimension="skills",
                                               criterion_class="flexible", supporting_evidence=["Excel"]),
                                         status="UNCLEAR" if state["n"] == 1 else "MATCHED")]}
            return _resp(json.dumps(body))

        client = MagicMock()
        client.chat.completions.create = create
        return client

    @pytest.mark.asyncio
    async def test_concurrent_v9_v10_runs_are_isolated(self):
        c = _compare_mod()
        v9, v10 = c.build_prompt_configs(_V9_ROW, "v10 prompt", 12000)
        from services.cv_evidence import CVFacts
        case = {"text": "MS Office user. " * 5, "analysis_json": ANALYSIS_JSON, "weights": {"weight_skills": 100}}
        facts = CVFacts(language="en", total_char_count=10)
        log: list = []
        clients = iter([self._slow_client(i, log) for i in range(8)])
        prompts = [v9, v10] * 4
        summary = AsyncMock(return_value=None)
        with patch.object(mapper_mod, "_get_mapper_client", side_effect=lambda: next(clients)), \
             c.mapper_hooks():
            results = await asyncio.gather(*(
                c.run_one(p, case, f"app-{i}", "job", DeterministicScoringConfig(), facts, None)
                for i, p in enumerate(prompts)))
        # every run: its own prompt on both calls, the right max_tokens, a repair, success
        for p, r in zip(prompts, results):
            assert r["ok"] and r["repair_used"], r
            want = [7000, 8000] if p["version"] == 9 else [12000, 12000]
            assert r["max_tokens_sent"] == want
            assert [cl["prompt_version_sent"] for cl in r["call_log"]] == [p["version"]] * 2
        # every OpenAI request carried the prompt matching its max_tokens
        assert len(log) == 16
        for _tag, mt, sent in log:
            assert (sent, mt) in {("v9", 7000), ("v9", 8000), ("v10", 12000)}
        summary.assert_not_called()
        # the module functions are restored afterwards
        assert mapper_mod._get_mapper_client is not None
        import services.ai_service as ai
        assert ai.load_active_prompt.__name__ == "load_active_prompt"

    @pytest.mark.asyncio
    async def test_hooks_restore_originals(self):
        c = _compare_mod()
        import services.ai_service as ai
        before = (mapper_mod._get_mapper_client, mapper_mod._generate_qualitative_summary, ai.load_active_prompt)
        with c.mapper_hooks():
            assert mapper_mod._get_mapper_client is not before[0]
        assert (mapper_mod._get_mapper_client, mapper_mod._generate_qualitative_summary,
                ai.load_active_prompt) == before

    @pytest.mark.asyncio
    async def test_run_one_requires_hooks(self):
        c = _compare_mod()
        v9, _ = c.build_prompt_configs(_V9_ROW, "v10 prompt", 12000)
        with pytest.raises(RuntimeError, match="mapper_hooks"):
            await c.run_one(v9, {}, "a", "j", DeterministicScoringConfig(), None, None)

    def test_check_request_guards(self):
        c = _compare_mod()
        v9, v10 = c.build_prompt_configs(_V9_ROW, "v10 prompt", 12000)
        ok = {"messages": [{"role": "system", "content": "v9 prompt" + "\n\nSECURITY SUFFIX"}]}
        c.check_request(v9, 0, ok)                         # security suffix appended is fine
        with pytest.raises(SystemExit, match="system prompt"):
            c.check_request(v9, 0, {"messages": [{"role": "system", "content": "v10 prompt"}]})
        with pytest.raises(SystemExit, match="system prompt"):
            c.check_request(v10, 0, {"messages": [{"role": "system", "content": "v9 prompt"}]})
        with pytest.raises(SystemExit, match="unexpected D-01 call #3"):
            c.check_request(v9, 2, ok)



# ═════════════════════════════════════════════════════════════════════════════
# 10. v10 evidence fix — no evidence-less PARTIAL/CD instructions; repair call
#     names the invalid item and offers only "quote" or "ABSENT"
# ═════════════════════════════════════════════════════════════════════════════

class TestEvidenceRuleWording:

    def _prompts(self):
        return {"contract": mapper_mod.D01_STATUS_CONTRACT,
                "fallback": mapper_mod._HARDCODED_SYSTEM_PROMPT,
                "v10": _builder().build_v10(TestPromptV10BuilderProductionV9Wording.V9_SNIPPET).text}

    @pytest.mark.parametrize("name", ["contract", "fallback", "v10"])
    def test_no_unconditional_years_not_met_partial(self, name):
        t = self._prompts()[name]
        assert "Years NOT met -> PARTIAL (known shortfall)" not in t
        assert "If years are NOT met → PARTIAL." not in t
        assert "even if\n  relevance is also unclear" not in t

    @pytest.mark.parametrize("name", ["contract", "fallback", "v10"])
    def test_evidence_rule_and_examples_present(self, name):
        t = self._prompts()[name]
        assert "EVIDENCE RULE: MATCHED, PARTIAL and CANNOT_DETERMINE are only valid with at least one quote" in t
        assert "status is ABSENT (rule 1 or 3)" in t and "Never invent" in t
        assert "Required years not being met is never, on its own, a reason for PARTIAL." in t
        assert '"Minimum 5 years of relevant HR experience" / CV shows 2 years in an HR role\n  -> PARTIAL, quoting the 2-year HR role.' in t
        assert "no HR role, HR duty or other HR evidence in the CV\n  -> ABSENT" in t
        assert "market or regulations -> ABSENT (not CANNOT_DETERMINE)" in t
        assert ('"Business Development Manager -\n  market entry and licensing" but not the country -> '
                'CANNOT_DETERMINE, cd_reason\n  "relevance_unverified", quoting the role.') in t

    def test_fallback_type_b_sentence(self):
        assert ("If years are NOT met → PARTIAL only when relevant\nexperience can be quoted; "
                "with nothing relevant to quote → ABSENT.") in mapper_mod._HARDCODED_SYSTEM_PROMPT

    def test_builder_e7_reported_when_missing(self):
        r = _builder().build_v10(_V9_LIKE.replace(
            "IMPORTANT: Only set status=MATCHED or status=PARTIAL", "IMPORTANT: something else"))
        assert any(n.startswith("E7 ") for n in r.not_found) and not r.errors

    def test_v9_itself_untouched_by_builder(self):
        src = TestPromptV10BuilderProductionV9Wording.V9_SNIPPET
        before = str(src)
        _builder().build_v10(src)
        assert src == before


class TestRepairNote:

    def _raw(self, *items):
        return json.dumps({"assessments": list(items)})

    @pytest.mark.parametrize("status", ["PARTIAL", CD, "MATCHED"])
    def test_evidence_error_names_item_and_offers_quote_or_absent(self, status):
        crit = "Minimum 5 years of relevant HR experience in a similar role (full text kept)"
        bad = _item(criterion_text=crit, status=status, supporting_evidence=[],
                    cd_reason="relevance_unverified" if status == CD else None)
        raw = self._raw(_item(), bad)
        note = mapper_mod._repair_note(_response_validation_errors(raw), raw)
        line = next(l for l in note.splitlines() if "assessment[1]" in l)
        assert f'"{crit}"' in line                                   # full criterion text
        assert f"you returned {status} with no supporting_evidence" in line
        assert "copy it exactly into supporting_evidence" in line   # option a: genuine quote
        assert "set status to ABSENT, cd_reason to null and supporting_evidence to []" in line  # option b
        assert "Do not invent or paraphrase a quote." in line
        assert "assessment[0]" not in note                            # valid items not listed
        assert "keep every other assessment unchanged" in note
        assert "Return the COMPLETE corrected JSON object" in note

    def test_never_suggests_inventing_or_keeping_cd_without_quote(self):
        raw = self._raw(_cd_item(supporting_evidence=[]))
        note = mapper_mod._repair_note(_response_validation_errors(raw), raw).lower()
        assert "invent evidence" in note and "never invent" in note
        for bad in ("keep cannot_determine", "add any quote", "make up", "placeholder"):
            assert bad not in note

    def test_other_errors_listed(self):
        raw = self._raw(_item(status="UNCLEAR"), _cd_item(cd_reason="extraction_gap"))
        note = mapper_mod._repair_note(_response_validation_errors(raw), raw)
        assert "status 'UNCLEAR'" in note and "invalid cd_reason 'extraction_gap'" in note

    def test_invalid_json_falls_back_to_error_list(self):
        note = mapper_mod._repair_note(["invalid JSON"], '{"assessments": [{"crit')
        assert "- invalid JSON" in note and "Return the COMPLETE corrected JSON object" in note


class TestRepairCallCarriesPreviousResponse:

    _PARTIAL_NO_EVIDENCE = _raw(_item(criterion_text="MS Office", dimension="skills", criterion_class="flexible",
                                      status="PARTIAL", supporting_evidence=[]))
    _FIXED_ABSENT = _raw(_item(criterion_text="MS Office", dimension="skills", criterion_class="flexible",
                               status="ABSENT", supporting_evidence=[], match_type="missing",
                               match_reason="No office software mentioned."))

    @pytest.mark.asyncio
    async def test_repair_messages_and_limits(self):
        client = _client(self._PARTIAL_NO_EVIDENCE, self._FIXED_ABSENT)
        await _assess(client, prompt={"system_prompt": "sys", "max_tokens": 12000})
        calls = client.chat.completions.create.await_args_list
        assert len(calls) == 2                                            # exactly one repair
        main, repair = calls[0].kwargs, calls[1].kwargs
        assert (main["max_tokens"], repair["max_tokens"]) == (12000, 12000)
        roles = [m["role"] for m in repair["messages"]]
        assert roles == ["system", "user", "assistant", "user"]
        assert repair["messages"][:2] == main["messages"]
        assert repair["messages"][2]["content"] == self._PARTIAL_NO_EVIDENCE
        assert '"MS Office": you returned PARTIAL with no supporting_evidence' in repair["messages"][3]["content"]

    @pytest.mark.asyncio
    async def test_partial_to_absent_repair_accepted_and_scored_absent(self):
        res = await _assess(_client(self._PARTIAL_NO_EVIDENCE, self._FIXED_ABSENT))
        assert res.absent_count == 1 and res.partial_count == 0
        d = deterministic_score_to_dict(DeterministicScoringEngine().score(res, {"weight_skills": 100}))
        crit = d["dimensions"]["skills"]["criteria"][0]
        assert crit["status"] == "ABSENT" and crit["verified_credit"] == 0.0
        assert d["required_summary"]["blocking_gaps"] == 1

    @pytest.mark.asyncio
    async def test_repeated_invalid_still_raises_after_one_repair(self):
        client = _client(self._PARTIAL_NO_EVIDENCE, self._PARTIAL_NO_EVIDENCE, self._FIXED_ABSENT)
        with pytest.raises(CriteriaMappingResponseError, match="PARTIAL without supporting_evidence"):
            await _assess(client)
        assert client.chat.completions.create.await_count == 2

    @pytest.mark.asyncio
    async def test_empty_previous_response_not_sent_as_assistant(self):
        client = _client("", self._FIXED_ABSENT)
        await _assess(client)
        roles = [m["role"] for m in client.chat.completions.create.await_args_list[1].kwargs["messages"]]
        assert roles == ["system", "user", "user"]

    def test_validator_unchanged_for_evidence_less_statuses(self):
        for status in ("MATCHED", "PARTIAL", CD):
            item = _item(status=status, supporting_evidence=[],
                         cd_reason="ambiguous" if status == CD else None)
            assert _validate_assessment(item) == f"{status} without supporting_evidence"
        assert _validate_assessment(_item(status="ABSENT", supporting_evidence=[], match_type="missing")) is None


class TestOfflineHarnessWithRepairMessages:
    """The comparison harness guards must accept the new repair message shape
    (system prompt still first) under concurrency."""

    @pytest.mark.asyncio
    async def test_concurrent_runs_with_assistant_message_repairs(self):
        c = _compare_mod()
        v9, v10 = c.build_prompt_configs(_V9_ROW, "v10 prompt", 12000)
        from services.cv_evidence import CVFacts
        case = {"text": "MS Office user. " * 5, "analysis_json": ANALYSIS_JSON, "weights": {"weight_skills": 100}}
        seen: list = []

        def make(i):
            n = {"k": 0}

            async def create(**kw):
                n["k"] += 1
                seen.append([m["role"] for m in kw["messages"]])
                await asyncio.sleep(0.01 * (i % 3 + 1))
                body = TestRepairCallCarriesPreviousResponse._PARTIAL_NO_EVIDENCE if n["k"] == 1 \
                    else TestRepairCallCarriesPreviousResponse._FIXED_ABSENT
                return _resp(body)
            cl = MagicMock()
            cl.chat.completions.create = create
            return cl

        clients = iter([make(i) for i in range(6)])
        prompts = [v9, v10] * 3
        with patch.object(mapper_mod, "_get_mapper_client", side_effect=lambda: next(clients)), c.mapper_hooks():
            results = await asyncio.gather(*(
                c.run_one(p, case, f"a{i}", "j", DeterministicScoringConfig(),
                          CVFacts(language="en", total_char_count=10), None) for i, p in enumerate(prompts)))
        for p, r in zip(prompts, results):
            assert r["ok"] and r["repair_used"]
            assert r["max_tokens_sent"] == ([7000, 8000] if p["version"] == 9 else [12000, 12000])
            assert [a["final_status"] for a in r["assessments"]] == ["ABSENT"]
        assert sorted(map(tuple, seen)).count(("system", "user", "assistant", "user")) == 6


# ═════════════════════════════════════════════════════════════════════════════
# 11. v10 CANNOT_DETERMINE refinement (prompt only; F-01 unchanged)
# ═════════════════════════════════════════════════════════════════════════════

class TestCannotDetermineRefinement:

    def _prompts(self):
        return {"contract": mapper_mod.D01_STATUS_CONTRACT,
                "fallback": mapper_mod._HARDCODED_SYSTEM_PROMPT,
                "v10": _builder().build_v10(TestPromptV10BuilderProductionV9Wording.V9_SNIPPET).text}

    @pytest.mark.parametrize("name", ["contract", "fallback", "v10"])
    def test_header_keeps_evidence_principles(self, name):
        t = self._prompts()[name]
        assert ("ASSESSMENT STATUS CONTRACT (authoritative for choosing the status; the evidence-interpretation "
                "principles elsewhere in these instructions still decide what the CV demonstrates):\n") in t
        assert "overrides any other status guidance" not in t

    @pytest.mark.parametrize("name", ["contract", "fallback", "v10"])
    def test_tightened_cd_definition_and_rules(self, name):
        t = self._prompts()[name]
        assert "Evidence genuinely relevant to this criterion establishes a meaningful part" in t
        assert "exactly ONE necessary\n                    fact is not stated" in t
        assert "match_reason must name that missing fact." in t
        assert "Adjacent, generic, speculative or merely related text is NOT relevant evidence -> ABSENT" in t
        assert 'the computed "Total Experience: X years" line\n  (it says nothing about relevance)' in t
        assert "A stated value that does not match is a known shortfall, not uncertainty" in t
        assert 'cd_reason "detail_missing" is only for a value that is\n  genuinely not stated.' in t
        assert "Evidence does not need the criterion's exact words" in t
        assert "Relevant CV information exists, but it is insufficient, ambiguous" not in t

    @pytest.mark.parametrize("name", ["contract", "fallback", "v10"])
    def test_relevant_experience_ladder(self, name):
        t = self._prompts()[name]
        assert "A total-years figure on its own establishes\n  nothing about relevance" in t
        assert "a) relevant role/activity evidenced and its duration meets N -> MATCHED;" in t
        assert "b) relevant role/activity evidenced but its duration is below N -> PARTIAL, quoting it;" in t
        assert 'relevant -> CANNOT_DETERMINE, cd_reason "relevance_unverified", quoting the role/activity' in t
        assert ("d) only a total-years figure, or only roles/activities unrelated to the criterion -> ABSENT\n"
                "     (never MATCHED or CANNOT_DETERMINE).") in t
        assert "years met but relevance not established -> CANNOT_DETERMINE" not in t

    @pytest.mark.parametrize("name", ["contract", "fallback", "v10"])
    def test_adjacent_domain_cd_examples_replaced(self, name):
        t = self._prompts()[name]
        for old in ("CV shows technical support roles, ICT relevance not stated",
                    "6 years total, education-sector relevance not stated -> CANNOT_DETERMINE",
                    "CV shows business roles but not where"):
            assert old not in t, old

    @pytest.mark.parametrize("name", ["contract", "fallback", "v10"])
    def test_new_examples_present(self, name):
        t = self._prompts()[name]
        for needle in (
            '"IT helpdesk: supported staff on company systems" but not\n  which systems -> CANNOT_DETERMINE',
            '"Teacher Assistant 2018-2021" and 6 years total',
            'Same criterion / CV shows only "Total Experience: 6 years" -> ABSENT.',
            '"Business Development Manager -\n  market entry and licensing" but not the country -> CANNOT_DETERMINE',
            '"Bachelor in Planetary Health" -> ABSENT',
            '"Full Stack Software Engineer" -> ABSENT.',
            'access control" -> MATCHED.',
            'Documentation" -> ABSENT.',
            '"Audited 300 payroll records monthly with zero discrepancies" -> MATCHED.',
            '"HR Assistant -\n  onboarding and staff records" with no dates for that role -> CANNOT_DETERMINE',
            # legitimate CD and earlier examples kept
            '"Palestinian construction sector" / CV shows construction experience',
            '"Bachelor\'s degree" with no field\n  -> CANNOT_DETERMINE, cd_reason "detail_missing".',
            "-> PARTIAL, quoting the 2-year HR role.",
        ):
            assert needle in t, needle

    @pytest.mark.parametrize("name", ["fallback", "v10"])
    def test_relevance_flag_definition(self, name):
        # v10 built from a v9 text that has the relevance_unverified flag line (E5)
        t = mapper_mod._HARDCODED_SYSTEM_PROMPT if name == "fallback" else _builder().build_v10(_V9_LIKE).text
        assert ("and a relevant role/activity is quoted, but its relevance to the required domain/function "
                "could not be confirmed (status CANNOT_DETERMINE). When only a total-years figure is "
                "available the status is ABSENT.") in t

    def test_fallback_type_b_sentence(self):
        t = mapper_mod._HARDCODED_SYSTEM_PROMPT
        assert ("are met and a genuinely relevant role/activity is quoted but its relevance\n"
                "cannot be fully established → CANNOT_DETERMINE") in t
        assert "If only a\ntotal-years figure is available → ABSENT" in t

    def test_builder_e4_total_years_only_becomes_absent(self):
        r = _builder().build_v10(TestPromptV10BuilderProductionV9Wording.V9_SNIPPET)
        assert any(a.startswith("E4 ") and "→ ABSENT" in a for a in r.applied)
        assert "status=ABSENT (a total-years figure alone does not establish relevance)" in r.text
        assert 'status=PARTIAL, match_type="inferred"' not in r.text
        assert "Assign\n  status=CANNOT_DETERMINE" not in r.text

    def test_builder_e4_never_touches_years_not_met(self):
        t = _builder().build_v10(_V9_LIKE).text
        assert "are NOT met → PARTIAL." in t
        assert "NOT met → ABSENT (a total-years" not in t and "NOT met → CANNOT_DETERMINE" not in t


# ═════════════════════════════════════════════════════════════════════════════
# F-01 backstop: CANNOT_DETERMINE supported only by the computed total-years line
# ═════════════════════════════════════════════════════════════════════════════

TOTAL_YEARS = "Total Experience: 4.2 years"
ROLE_QUOTE = "HR Officer, Gulf Bank (2019 - 2024)"
RELEVANT = "Minimum 7 years of relevant experience"


def _score_with_evidence(statuses, evidence, cd_reasons=None, crit=CRIT, local=None, weights=W):
    """Score with per-criterion supporting_evidence / cd_reason overrides."""
    llm = _llm(statuses, crit=crit)
    for a in llm.assessments:
        if a.criterion_text in evidence:
            a.supporting_evidence = list(evidence[a.criterion_text])
        if cd_reasons and a.criterion_text in cd_reasons:
            a.cd_reason = cd_reasons[a.criterion_text]
    before = llm_matchresult_to_dict(llm)
    d = deterministic_score_to_dict(DeterministicScoringEngine().score(llm, weights, local))
    return d, before, llm


def _crit_of(d, text):
    return next(c for dim in d["dimensions"].values() for c in dim["criteria"] if c["criterion_text"] == text)


class TestTotalYearsOnlyBackstop:

    # 1. model CD + total-years-only → ABSENT
    def test_model_cd_total_years_only_becomes_absent(self):
        d, _, _ = _score_with_evidence({EXP: CD}, {EXP: [TOTAL_YEARS]})
        c = _crit_of(d, EXP)
        assert c["status"] == "ABSENT"
        assert c["cd_reason"] is None
        assert "total_years_only_evidence" in c["risk_flags"]
        assert c["reconciled_from"] == {"status": CD, "cd_reason": "relevance_unverified",
                                        "rule": "total_years_only_evidence"}
        assert (c["verified_credit"], c["upper_credit"], c["pending_worth"]) == (0.0, 0.0, 0.0)
        assert c["supporting_evidence"] == [TOTAL_YEARS]          # evidence kept for audit

    # 2. relevance-step CD (LLM MATCHED + local PARTIAL relevance-unverified)
    def test_relevance_step_cd_total_years_only_becomes_absent(self):
        local = [{"criterion_text": RELEVANT, "status": "PARTIAL", "confidence": 0.45,
                  "partial_reason": "8 years total experience, but relevance to role not verified",
                  "dimension": "experience", "required": True,
                  "supporting_evidence": ["8.0 years total experience extracted from CV"]}]
        d, _, _ = _score_with_evidence({}, {RELEVANT: ["Total Experience: 8.0 years"]},
                                       crit=[(RELEVANT, "experience", True)], local=local,
                                       weights={"weight_experience": 100})
        c = _crit_of(d, RELEVANT)
        assert c["status"] == "ABSENT" and c["cd_reason"] is None
        assert c["reconciled_from"] == {"status": CD, "cd_reason": "relevance_unverified",
                                        "rule": "total_years_only_evidence"}
        for flag in ("llm_local_relevance_disagreement", "local_relevance_check",
                     "total_years_only_evidence"):
            assert flag in c["risk_flags"]
        assert c["upper_credit"] == 0.0

    # 3. a genuine CV quote preserves CD
    @pytest.mark.parametrize("evidence", [
        [TOTAL_YEARS, ROLE_QUOTE], [ROLE_QUOTE], [ROLE_QUOTE, TOTAL_YEARS, ""],
    ])
    def test_genuine_quote_preserves_cd(self, evidence):
        d, _, _ = _score_with_evidence({EXP: CD}, {EXP: evidence})
        c = _crit_of(d, EXP)
        assert c["status"] == CD and c["cd_reason"] == "relevance_unverified"
        assert "total_years_only_evidence" not in c["risk_flags"]
        assert "reconciled_from" not in c
        assert c["upper_credit"] == 1.0

    # 4. detail_missing CD with a genuine quote is preserved
    def test_detail_missing_cd_with_quote_preserved(self):
        d, _, _ = _score_with_evidence({EXP: CD}, {EXP: [ROLE_QUOTE]}, {EXP: "detail_missing"})
        c = _crit_of(d, EXP)
        assert c["status"] == CD and c["cd_reason"] == "detail_missing"
        assert "reconciled_from" not in c

    def test_reconciled_from_records_original_cd_reason(self):
        d, _, _ = _score_with_evidence({EXP: CD}, {EXP: [TOTAL_YEARS]}, {EXP: "detail_missing"})
        c = _crit_of(d, EXP)
        assert c["status"] == "ABSENT"
        assert c["reconciled_from"]["cd_reason"] == "detail_missing"

    # 5. regex edge cases
    @pytest.mark.parametrize("evidence, reconciled", [
        (["Total Experience: 4.2 years"], True),
        (['"Total Experience: 4.2 years"'], True),
        (["'Total Experience: 4.2 years'"], True),
        (["total experience: 4.2 YEARS"], True),
        (["Total Experience: 4.2 years."], True),
        (["Total Experience: 4 years"], True),
        (["Total Experience: 1.0 year"], True),
        (["  Total Experience: 6.2 years  "], True),
        (["Total Experience: 4.2 years", "Total Experience: 4.2 years"], True),
        (["Total Experience: 4.2 years", "", "   "], True),
        (["Total Experience: 5.0 years at Ministry of Health"], False),
        (["Total Experience: 4.2 years in HR"], False),
        (["Highest Education: Bachelor of Commerce"], False),
        (["Total Experience: 4.2 years", "Highest Education: Bachelor of Commerce"], False),
        (["4.2 years"], False),
    ])
    def test_regex_edge_cases(self, evidence, reconciled):
        d, _, _ = _score_with_evidence({EXP: CD}, {EXP: evidence})
        c = _crit_of(d, EXP)
        assert (c["status"] == "ABSENT") is reconciled
        assert ("total_years_only_evidence" in c["risk_flags"]) is reconciled

    # 6. pure-duration criteria are excluded
    @pytest.mark.parametrize("text", [
        "Minimum 3 years of experience", "Minimum 3 years experience",
        "Minimum 5 years of professional experience", "minimum 2 years of work experience.",
        "Minimum 1 year of total experience",
    ])
    def test_pure_duration_cd_unchanged(self, text):
        d, _, _ = _score_with_evidence({text: CD}, {text: [TOTAL_YEARS]},
                                       crit=[(text, "experience", True)],
                                       weights={"weight_experience": 100})
        c = _crit_of(d, text)
        assert c["status"] == CD and c["cd_reason"] == "relevance_unverified"
        assert "reconciled_from" not in c and c["upper_credit"] == 1.0

    # 7. MATCHED / PARTIAL / ABSENT with total-years-only are never touched
    @pytest.mark.parametrize("status", ["MATCHED", "PARTIAL", "ABSENT"])
    def test_non_cd_statuses_unchanged(self, status):
        d, _, _ = _score_with_evidence({EXP: status}, {EXP: [TOTAL_YEARS]})
        c = _crit_of(d, EXP)
        assert c["status"] == status
        assert "total_years_only_evidence" not in c["risk_flags"]
        assert "reconciled_from" not in c

    def test_other_criteria_untouched(self):
        base = _score({EXP: CD})
        d, _, _ = _score_with_evidence({EXP: CD}, {EXP: [TOTAL_YEARS]})
        for text, _dim, _req in CRIT:
            if text != EXP:
                assert _crit_of(d, text) == _crit_of(base, text)

    # 8. blocking gaps, pending/upper and recommendation impact
    def test_blocking_pending_upper_and_recommendation(self):
        statuses = GOLDEN["B"][0]                       # EXP CD → needs_verification
        base = _score(statuses)
        assert base["recommendation"] == "needs_verification"
        d, _, _ = _score_with_evidence(statuses, {EXP: [TOTAL_YEARS]})
        assert d["verified_score"] == base["verified_score"] == 69
        assert d["upper_score"] == 69 and d["pending_points"] == 0
        assert d["verification_items"] == []
        assert d["required_summary"]["cannot_determine"] == 0
        assert d["required_summary"]["absent"] == 1
        assert d["required_summary"]["blocking_gaps"] == 1
        assert d["recommendation"] == d["decision_verified_basis"] == "partial"

    # 9. llm_match_results_json keeps the original assessment
    def test_llm_match_results_unchanged(self):
        d, before, llm = _score_with_evidence({EXP: CD}, {EXP: [TOTAL_YEARS]})
        assert _crit_of(d, EXP)["status"] == "ABSENT"
        assert llm_matchresult_to_dict(llm) == before
        a = next(x for x in before["assessments"] if x["criterion_text"] == EXP)
        assert a["status"] == CD and a["cd_reason"] == "relevance_unverified"
        assert a["supporting_evidence"] == [TOTAL_YEARS]
        assert "total_years_only_evidence" not in (a.get("risk_flags") or [])
