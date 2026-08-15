"""
Tests for F-01.2 backfill_deterministic_scores.py.

Tests the pure / database-free functions only:
  - _parse_weights
  - _process_row
  - _load_scoring_config (via a mock connection)
  - run_backfill dry-run behaviour (via mock DB helpers)
  - Idempotency: a row already updated returns skipped, not updated
  - Failure isolation: one bad row does not stop remaining rows
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ── Module under test ─────────────────────────────────────────────────────────
from scripts.backfill_deterministic_scores import (
    _apply_soft_skill_routing,
    _parse_weights,
    _process_row,
)
from services.deterministic_scoring import DeterministicScoringConfig
from services.llm_criteria_mapper import LLMCriterionAssessment, LLMMatchResult


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_llm_result(
    assessments: list[LLMCriterionAssessment] | None = None,
) -> LLMMatchResult:
    if assessments is None:
        assessments = [
            LLMCriterionAssessment(
                criterion_text="Python",
                dimension="skills",
                required=True,
                status="MATCHED",
                confidence=0.90,
                supporting_evidence=["Candidate listed Python as primary language."],
                match_reason="Direct match.",
                match_type="direct",
                criterion_class="strict",
                risk_flags=[],
                prompt_code="recruitment.criteria_mapping",
                prompt_version="2",
                llm_model="gpt-4o-mini",
            )
        ]
    return LLMMatchResult(
        application_id="app-001",
        job_id="job-001",
        assessments=assessments,
        processing_ms=120,
        created_at="2026-01-01T00:00:00+00:00",
        prompt_code="recruitment.criteria_mapping",
        prompt_version="2",
        model="gpt-4o-mini",
        mapper_version="1.2.3",
        total_criteria=len(assessments),
        matched_count=sum(1 for a in assessments if a.status == "MATCHED"),
        partial_count=sum(1 for a in assessments if a.status == "PARTIAL"),
        absent_count=sum(1 for a in assessments if a.status == "ABSENT"),
        high_confidence_count=0,
        low_confidence_count=0,
    )


_DEFAULT_WEIGHTS: dict[str, int] = {
    "weight_skills":           30,
    "weight_experience":       25,
    "weight_education":        15,
    "weight_certifications":   10,
    "weight_soft_skills":      10,
    "weight_domain_knowledge":  5,
    "weight_other":             5,
}

_DEFAULT_CFG = DeterministicScoringConfig()


# ── _parse_weights ────────────────────────────────────────────────────────────

class TestParseWeights:
    def test_dict_input(self):
        w = _parse_weights({"weight_skills": 30, "weight_experience": 25})
        assert w["weight_skills"] == 30
        assert w["weight_experience"] == 25

    def test_string_input(self):
        raw = json.dumps({"weight_skills": 30, "weight_experience": 25})
        w = _parse_weights(raw)
        assert w["weight_skills"] == 30

    def test_float_values_coerced_to_int(self):
        """JSONB may return numeric as float; must coerce to int."""
        w = _parse_weights({"weight_skills": 30.0, "weight_experience": 25.0})
        assert isinstance(w["weight_skills"], int)
        assert w["weight_skills"] == 30

    def test_full_seven_weights(self):
        w = _parse_weights(_DEFAULT_WEIGHTS)
        assert len(w) == 7
        assert sum(w.values()) == 100

    def test_invalid_type_raises(self):
        with pytest.raises((ValueError, TypeError)):
            _parse_weights(None)

    def test_non_dict_non_str_raises(self):
        with pytest.raises((ValueError, TypeError)):
            _parse_weights([30, 25, 15])

    def test_invalid_json_string_raises(self):
        with pytest.raises((json.JSONDecodeError, ValueError)):
            _parse_weights("not-json")


# ── _process_row ──────────────────────────────────────────────────────────────

class TestProcessRow:
    def _serialised_llm(self, assessments=None) -> dict[str, Any]:
        """Build a llm_match_results_json dict as stored in the DB (JSONB → dict)."""
        from services.evidence_serialiser import llm_matchresult_to_dict
        return llm_matchresult_to_dict(_make_llm_result(assessments))

    def test_returns_int_score_and_json_string(self):
        llm_json = self._serialised_llm()
        score, json_str, _ = _process_row(llm_json, _DEFAULT_WEIGHTS, _DEFAULT_CFG)
        assert isinstance(score, int)
        assert 0 <= score <= 100
        assert isinstance(json_str, str)

    def test_json_string_is_valid_json(self):
        llm_json = self._serialised_llm()
        _, json_str, _ = _process_row(llm_json, _DEFAULT_WEIGHTS, _DEFAULT_CFG)
        parsed = json.loads(json_str)
        assert parsed["_schema"] == "det_score_v2"
        assert "final_score" in parsed
        assert "dimensions" in parsed

    def test_all_matched_gives_high_score(self):
        assessments = [
            LLMCriterionAssessment(
                criterion_text=f"Criterion {i}", dimension=dim, required=True,
                status="MATCHED", confidence=0.90,
                supporting_evidence=["Evidence text."],
                match_reason="Direct match.", match_type="direct",
                criterion_class=cls, risk_flags=[],
                prompt_code="recruitment.criteria_mapping", prompt_version="2",
                llm_model="gpt-4o-mini",
            )
            for i, (dim, cls) in enumerate([
                ("skills", "strict"), ("experience", "experience"),
                ("education", "education"), ("certifications", "certification"),
                ("soft_skills", "soft_skill"), ("domain_knowledge", "domain_knowledge"),
                ("other", "other"),
            ])
        ]
        from services.evidence_serialiser import llm_matchresult_to_dict
        llm_json = llm_matchresult_to_dict(_make_llm_result(assessments))
        score, _, _ = _process_row(llm_json, _DEFAULT_WEIGHTS, _DEFAULT_CFG)
        assert score == 100

    def test_all_absent_gives_zero(self):
        assessments = [
            LLMCriterionAssessment(
                criterion_text="Missing skill", dimension="skills", required=True,
                status="ABSENT", confidence=0.0, supporting_evidence=[],
                match_reason="No evidence.", match_type="missing",
                criterion_class="strict", risk_flags=[],
                prompt_code="recruitment.criteria_mapping", prompt_version="2",
                llm_model="gpt-4o-mini",
            )
        ]
        from services.evidence_serialiser import llm_matchresult_to_dict
        llm_json = llm_matchresult_to_dict(_make_llm_result(assessments))
        score, _, _ = _process_row(llm_json, _DEFAULT_WEIGHTS, _DEFAULT_CFG)
        assert score == 0

    def test_weights_as_string_also_work(self):
        """weights_snapshot may arrive as JSON string in edge cases."""
        llm_json = self._serialised_llm()
        weights_str = json.dumps(_DEFAULT_WEIGHTS)
        score, _, _ = _process_row(llm_json, weights_str, _DEFAULT_CFG)
        assert isinstance(score, int)

    def test_partial_credit_config_affects_score(self):
        """Higher partial_credit should produce higher score for PARTIAL criteria."""
        assessments = [
            LLMCriterionAssessment(
                criterion_text="Team leadership", dimension="soft_skills",
                required=True, status="PARTIAL", confidence=0.60,
                supporting_evidence=["Led a small team."],
                match_reason="Partial evidence.", match_type="direct",
                criterion_class="soft_skill", risk_flags=[],
                prompt_code="recruitment.criteria_mapping", prompt_version="2",
                llm_model="gpt-4o-mini",
            )
        ]
        from services.evidence_serialiser import llm_matchresult_to_dict
        llm_json = llm_matchresult_to_dict(_make_llm_result(assessments))

        cfg_low  = DeterministicScoringConfig(partial_credit=0.30)
        cfg_high = DeterministicScoringConfig(partial_credit=0.70)

        score_low,  _, _ = _process_row(llm_json, _DEFAULT_WEIGHTS, cfg_low)
        score_high, _, _ = _process_row(llm_json, _DEFAULT_WEIGHTS, cfg_high)
        assert score_high > score_low

    def test_invalid_llm_json_raises(self):
        with pytest.raises(Exception):
            _process_row("not-a-dict", _DEFAULT_WEIGHTS, _DEFAULT_CFG)

    def test_invalid_weights_raises(self):
        llm_json = self._serialised_llm()
        with pytest.raises(Exception):
            _process_row(llm_json, None, _DEFAULT_CFG)

    def test_empty_assessments_gives_zero(self):
        """No criteria → all dimension averages are 0 → final_score = 0."""
        from services.evidence_serialiser import llm_matchresult_to_dict
        llm_json = llm_matchresult_to_dict(_make_llm_result([]))
        score, _, _ = _process_row(llm_json, _DEFAULT_WEIGHTS, _DEFAULT_CFG)
        assert score == 0

    def test_det_score_json_schema_key(self):
        llm_json = self._serialised_llm()
        _, json_str, _ = _process_row(llm_json, _DEFAULT_WEIGHTS, _DEFAULT_CFG)
        parsed = json.loads(json_str)
        assert parsed["_schema"] == "det_score_v2"

    def test_mapper_version_preserved_in_output(self):
        llm_json = self._serialised_llm()
        _, json_str, _ = _process_row(llm_json, _DEFAULT_WEIGHTS, _DEFAULT_CFG)
        parsed = json.loads(json_str)
        assert parsed.get("mapper_version") == "1.2.3"

    def test_deterministic_same_input_same_output(self):
        """Same input always produces the same output (no randomness)."""
        llm_json = self._serialised_llm()
        score1, json1, _ = _process_row(llm_json, _DEFAULT_WEIGHTS, _DEFAULT_CFG)
        score2, json2, _ = _process_row(llm_json, _DEFAULT_WEIGHTS, _DEFAULT_CFG)
        assert score1 == score2
        assert json.loads(json1)["final_score"] == json.loads(json2)["final_score"]

    def test_output_contains_required_and_preferred_summary(self):
        """D-02-A: det_score_json must contain required_summary and preferred_summary."""
        llm_json = self._serialised_llm()
        _, json_str, _ = _process_row(llm_json, _DEFAULT_WEIGHTS, _DEFAULT_CFG)
        parsed = json.loads(json_str)
        assert "required_summary" in parsed
        assert "preferred_summary" in parsed
        assert "recruiter_signal" in parsed
        assert "recruiter_label" in parsed

    def test_rerouted_count_returned(self):
        """Third return value is count of assessments whose dimension was corrected."""
        assessments = [
            LLMCriterionAssessment(
                criterion_text="Communication", dimension="skills",  # wrong dimension
                required=True, status="MATCHED", confidence=0.80,
                supporting_evidence=["Strong communicator noted throughout."],
                match_reason="Evidence found.", match_type="inferred",
                criterion_class="soft_skill",  # should be in soft_skills
                risk_flags=[],
                prompt_code="recruitment.criteria_mapping", prompt_version="2",
                llm_model="gpt-4o-mini",
            ),
            LLMCriterionAssessment(
                criterion_text="Python", dimension="skills",  # correct dimension
                required=True, status="MATCHED", confidence=0.95,
                supporting_evidence=["Python listed."],
                match_reason="Direct match.", match_type="direct",
                criterion_class="strict",
                risk_flags=[],
                prompt_code="recruitment.criteria_mapping", prompt_version="2",
                llm_model="gpt-4o-mini",
            ),
        ]
        from services.evidence_serialiser import llm_matchresult_to_dict
        llm_json = llm_matchresult_to_dict(_make_llm_result(assessments))
        _, _, rerouted = _process_row(llm_json, _DEFAULT_WEIGHTS, _DEFAULT_CFG)
        assert rerouted == 1  # only the soft_skill criterion was rerouted

    def test_rerouted_soft_skill_lands_in_soft_skills_bucket(self):
        """After routing correction the det engine scores the criterion in soft_skills."""
        assessments = [
            LLMCriterionAssessment(
                criterion_text="Teamwork", dimension="skills",  # pre-F-01.4 misrouting
                required=True, status="MATCHED", confidence=0.85,
                supporting_evidence=["Led cross-functional projects."],
                match_reason="Evidence found.", match_type="transferable",
                criterion_class="soft_skill",
                risk_flags=[],
                prompt_code="recruitment.criteria_mapping", prompt_version="2",
                llm_model="gpt-4o-mini",
            ),
        ]
        from services.evidence_serialiser import llm_matchresult_to_dict
        llm_json = llm_matchresult_to_dict(_make_llm_result(assessments))
        _, json_str, rerouted = _process_row(llm_json, _DEFAULT_WEIGHTS, _DEFAULT_CFG)
        parsed = json.loads(json_str)
        assert rerouted == 1
        # soft_skills dimension must have the required criterion; skills must have none
        ss = parsed["dimensions"]["soft_skills"]
        assert ss["n_required"] + ss["n_preferred"] == 1
        sk = parsed["dimensions"]["skills"]
        assert sk["n_required"] + sk["n_preferred"] == 0

    def test_no_rerouting_when_already_correct(self):
        """Criteria already in soft_skills dimension are not double-counted."""
        assessments = [
            LLMCriterionAssessment(
                criterion_text="Leadership", dimension="soft_skills",
                required=True, status="MATCHED", confidence=0.90,
                supporting_evidence=["Managed a team of 8."],
                match_reason="Direct evidence.", match_type="direct",
                criterion_class="soft_skill",
                risk_flags=[],
                prompt_code="recruitment.criteria_mapping", prompt_version="2",
                llm_model="gpt-4o-mini",
            ),
        ]
        from services.evidence_serialiser import llm_matchresult_to_dict
        llm_json = llm_matchresult_to_dict(_make_llm_result(assessments))
        _, _, rerouted = _process_row(llm_json, _DEFAULT_WEIGHTS, _DEFAULT_CFG)
        assert rerouted == 0  # already correct — no rerouting needed


# ── _apply_soft_skill_routing ─────────────────────────────────────────────────

class TestApplySoftSkillRouting:
    """Unit tests for the F-01.6 in-memory routing correction function."""

    def _make_assessment(self, criterion_class: str, dimension: str) -> LLMCriterionAssessment:
        return LLMCriterionAssessment(
            criterion_text="Test criterion",
            dimension=dimension,
            required=True,
            status="MATCHED",
            confidence=0.80,
            supporting_evidence=["Some evidence."],
            match_reason="Matches.",
            match_type="direct",
            criterion_class=criterion_class,
            risk_flags=[],
            prompt_code="recruitment.criteria_mapping",
            prompt_version="2",
            llm_model="gpt-4o-mini",
        )

    def _make_result(self, assessments) -> LLMMatchResult:
        return LLMMatchResult(
            application_id="app-test",
            job_id="job-test",
            assessments=assessments,
            processing_ms=100,
            created_at="2026-01-01T00:00:00+00:00",
            prompt_code="recruitment.criteria_mapping",
            prompt_version="2",
            model="gpt-4o-mini",
        )

    def test_soft_skill_in_skills_is_corrected(self):
        a = self._make_assessment("soft_skill", "skills")
        r = self._make_result([a])
        count = _apply_soft_skill_routing(r)
        assert count == 1
        assert r.assessments[0].dimension == "soft_skills"

    def test_soft_skill_in_experience_is_corrected(self):
        a = self._make_assessment("soft_skill", "experience")
        r = self._make_result([a])
        count = _apply_soft_skill_routing(r)
        assert count == 1
        assert r.assessments[0].dimension == "soft_skills"

    def test_soft_skill_already_correct_not_counted(self):
        a = self._make_assessment("soft_skill", "soft_skills")
        r = self._make_result([a])
        count = _apply_soft_skill_routing(r)
        assert count == 0
        assert r.assessments[0].dimension == "soft_skills"

    def test_soft_skill_in_other_preserved(self):
        """explicit 'other' classification is intentional — do not override."""
        a = self._make_assessment("soft_skill", "other")
        r = self._make_result([a])
        count = _apply_soft_skill_routing(r)
        assert count == 0
        assert r.assessments[0].dimension == "other"

    def test_non_soft_skill_class_not_affected(self):
        a = self._make_assessment("strict", "skills")
        r = self._make_result([a])
        count = _apply_soft_skill_routing(r)
        assert count == 0
        assert r.assessments[0].dimension == "skills"

    def test_mixed_assessments_only_soft_skill_corrected(self):
        assessments = [
            self._make_assessment("soft_skill", "skills"),    # should be corrected
            self._make_assessment("strict", "skills"),         # must not change
            self._make_assessment("soft_skill", "soft_skills"), # already correct
            self._make_assessment("soft_skill", "experience"),  # should be corrected
        ]
        r = self._make_result(assessments)
        count = _apply_soft_skill_routing(r)
        assert count == 2
        assert r.assessments[0].dimension == "soft_skills"
        assert r.assessments[1].dimension == "skills"          # unchanged
        assert r.assessments[2].dimension == "soft_skills"     # unchanged
        assert r.assessments[3].dimension == "soft_skills"

    def test_empty_assessments_returns_zero(self):
        r = self._make_result([])
        assert _apply_soft_skill_routing(r) == 0

    def test_mutates_in_place_not_input_dict(self):
        """llm_match_results_json input dict is never modified."""
        from services.evidence_serialiser import llm_matchresult_to_dict, llm_matchresult_from_dict
        assessment = self._make_assessment("soft_skill", "skills")
        result = self._make_result([assessment])
        original_dict = llm_matchresult_to_dict(result)
        # Check stored value before
        stored_dim = original_dict["assessments"][0]["dimension"]
        assert stored_dim == "skills"

        # Deserialise fresh copy and apply routing
        fresh = llm_matchresult_from_dict(original_dict)
        _apply_soft_skill_routing(fresh)

        # Original dict must be unchanged
        assert original_dict["assessments"][0]["dimension"] == "skills"
        # But fresh object is corrected
        assert fresh.assessments[0].dimension == "soft_skills"


# ── Idempotency — _update_row guard ──────────────────────────────────────────

class TestIdempotency:
    """
    Simulate what happens when a row is already processed.
    _fetch_row returns None (due to WHERE det_final_score IS NULL guard),
    so the row is counted as skipped, not updated.
    """

    def test_already_processed_row_is_skipped(self):
        """Simulate _fetch_row returning None → skipped."""
        from scripts.backfill_deterministic_scores import _fetch_row
        # When _fetch_row returns None (already processed), we expect skip
        # We test this by verifying the guard is in the SQL
        import inspect
        source = inspect.getsource(_fetch_row)
        assert "det_final_score IS NULL" in source

    def test_update_row_guard_in_sql(self):
        """Verify _update_row has WHERE det_final_score IS NULL guard."""
        from scripts.backfill_deterministic_scores import _update_row
        import inspect
        source = inspect.getsource(_update_row)
        assert "det_final_score IS NULL" in source

    def test_process_row_is_pure_no_side_effects(self):
        """_process_row does not modify its inputs."""
        from services.evidence_serialiser import llm_matchresult_to_dict
        llm_json = llm_matchresult_to_dict(_make_llm_result())
        original_keys = set(llm_json.keys())
        _process_row(llm_json, _DEFAULT_WEIGHTS.copy(), _DEFAULT_CFG)
        assert set(llm_json.keys()) == original_keys


# ── run_backfill — dry-run mode ───────────────────────────────────────────────

class TestRunBackfillDryRun:
    """
    Test run_backfill in dry-run mode using mocked DB helpers.
    No actual database connection required.
    """

    def _make_db_row(self, app_id: str = "app-001") -> dict[str, Any]:
        from services.evidence_serialiser import llm_matchresult_to_dict
        return {
            "llm_match_results_json": llm_matchresult_to_dict(_make_llm_result()),
            "weights_snapshot": _DEFAULT_WEIGHTS,
        }

    def test_dry_run_does_not_call_update(self):
        """In dry-run mode, _update_row must never be called."""
        from services.evidence_serialiser import llm_matchresult_to_dict

        # Build a minimal fake row
        fake_row = {
            "llm_match_results_json": llm_matchresult_to_dict(_make_llm_result()),
            "weights_snapshot": _DEFAULT_WEIGHTS,
        }

        with (
            patch(
                "scripts.backfill_deterministic_scores._get_connection"
            ) as mock_conn_factory,
            patch(
                "scripts.backfill_deterministic_scores._load_scoring_config",
                return_value=_DEFAULT_CFG,
            ),
            patch(
                "scripts.backfill_deterministic_scores._count_pending",
                return_value=1,
            ),
            patch(
                "scripts.backfill_deterministic_scores._fetch_pending_ids",
                return_value=["app-001"],
            ),
            patch(
                "scripts.backfill_deterministic_scores._fetch_row",
                return_value=fake_row,
            ),
            patch(
                "scripts.backfill_deterministic_scores._update_row"
            ) as mock_update,
        ):
            mock_conn_factory.return_value = MagicMock()
            from scripts.backfill_deterministic_scores import run_backfill
            run_backfill(dry_run=True, limit=None, batch_size=100, verbose=False)
            mock_update.assert_not_called()

    def test_dry_run_zero_pending_exits_cleanly(self):
        with (
            patch(
                "scripts.backfill_deterministic_scores._get_connection"
            ) as mock_conn_factory,
            patch(
                "scripts.backfill_deterministic_scores._load_scoring_config",
                return_value=_DEFAULT_CFG,
            ),
            patch(
                "scripts.backfill_deterministic_scores._count_pending",
                return_value=0,
            ),
        ):
            mock_conn_factory.return_value = MagicMock()
            from scripts.backfill_deterministic_scores import run_backfill
            # Should not raise, just log "already complete"
            run_backfill(dry_run=True, limit=None, batch_size=100, verbose=False)


# ── Failure isolation ─────────────────────────────────────────────────────────

class TestFailureIsolation:
    """
    One row failing should not abort the remaining rows.
    """

    def test_bad_row_does_not_stop_batch(self):
        """When one row raises, other rows are still processed."""
        from services.evidence_serialiser import llm_matchresult_to_dict

        good_row = {
            "llm_match_results_json": llm_matchresult_to_dict(_make_llm_result()),
            "weights_snapshot": _DEFAULT_WEIGHTS,
        }
        bad_row = {
            "llm_match_results_json": llm_matchresult_to_dict(_make_llm_result()),
            "weights_snapshot": None,   # None causes _parse_weights to raise ValueError
        }

        call_count = {"n": 0}

        def _side_effect(conn, app_id, *, recalculate=False):
            call_count["n"] += 1
            if app_id == "app-bad":
                return bad_row
            return good_row

        update_calls = []

        with (
            patch(
                "scripts.backfill_deterministic_scores._get_connection"
            ) as mock_conn_factory,
            patch(
                "scripts.backfill_deterministic_scores._load_scoring_config",
                return_value=_DEFAULT_CFG,
            ),
            patch(
                "scripts.backfill_deterministic_scores._count_pending",
                return_value=3,
            ),
            patch(
                "scripts.backfill_deterministic_scores._fetch_pending_ids",
                return_value=["app-001", "app-bad", "app-002"],
            ),
            patch(
                "scripts.backfill_deterministic_scores._fetch_row",
                side_effect=_side_effect,
            ),
            patch(
                "scripts.backfill_deterministic_scores._update_row",
                side_effect=lambda conn, aid, score, jsn, *, recalculate=False: (
                    update_calls.append(aid) or True
                ),
            ),
        ):
            mock_conn = MagicMock()
            mock_conn_factory.return_value = mock_conn
            from scripts.backfill_deterministic_scores import run_backfill
            # Should complete without raising even though app-bad fails
            run_backfill(dry_run=False, limit=None, batch_size=100, verbose=False)

        # Both good rows should have been updated
        assert "app-001" in update_calls
        assert "app-002" in update_calls
        # Bad row should not appear in updates
        assert "app-bad" not in update_calls


# ── _load_scoring_config ──────────────────────────────────────────────────────

class TestLoadScoringConfig:
    def test_falls_back_to_defaults_when_no_rows(self):
        """When system_config has no det_* keys, defaults are used."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = lambda s: s
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cursor

        from scripts.backfill_deterministic_scores import _load_scoring_config
        cfg = _load_scoring_config(mock_conn)

        defaults = DeterministicScoringConfig()
        assert cfg.partial_credit == defaults.partial_credit
        assert cfg.required_weight == defaults.required_weight
        assert cfg.preferred_weight == defaults.preferred_weight
        assert not cfg.enable_required_absent_floor

    def test_overrides_from_system_config(self):
        """Values from system_config should override defaults."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = lambda s: s
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchall.return_value = [
            {"key": "scoring_v2.det_partial_credit", "value": "0.60"},
            {"key": "scoring_v2.det_required_weight", "value": "0.65"},
            {"key": "scoring_v2.det_preferred_weight", "value": "0.35"},
        ]
        mock_conn.cursor.return_value = mock_cursor

        from scripts.backfill_deterministic_scores import _load_scoring_config
        cfg = _load_scoring_config(mock_conn)

        assert cfg.partial_credit == pytest.approx(0.60)
        assert cfg.required_weight == pytest.approx(0.65)
        assert cfg.preferred_weight == pytest.approx(0.35)
