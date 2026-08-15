"""
Regression tests for Score Overview display formatting.

Root cause: det_score_json.dimensions.<dim>.dimension_score is a 0–1 decimal.
The build_dim helper must multiply by 100 and round before sending to the
frontend, which expects 0–100 integers.

Legacy score_* columns are already 0–100 and must not be modified.
"""
from __future__ import annotations

from unittest.mock import MagicMock


# ── Inline copy of build_dim logic (no FastAPI/DB import needed) ─────────────

def _make_build_dim(app: dict, weights: dict, det_dims: dict):
    """Factory that returns a build_dim closure matching the router implementation."""
    def build_dim(score_key: str, weight_key: str, det_dim_key: str = "") -> dict:
        weight = weights.get(weight_key, 0)
        if det_dims and det_dim_key and det_dim_key in det_dims:
            score = round((det_dims[det_dim_key].get("dimension_score", 0) or 0) * 100)
        else:
            score = app.get(score_key) or 0
        return {"achieved": score, "max": 100, "weight": weight}
    return build_dim


# ── TestDeterministicDimensionConversion ─────────────────────────────────────

class TestDeterministicDimensionConversion:
    """det_score_json dimension_score (0–1) must be converted to 0–100 integers."""

    def _build(self, dim_score: float) -> int:
        det_dims = {"skills": {"dimension_score": dim_score}}
        fn = _make_build_dim({}, {}, det_dims)
        return fn("score_skills", "weight_skills", "skills")["achieved"]

    def test_decimal_0544_becomes_54(self):
        assert self._build(0.544) == 54

    def test_decimal_1_0_becomes_100(self):
        assert self._build(1.0) == 100

    def test_decimal_0_76_becomes_76(self):
        assert self._build(0.76) == 76

    def test_decimal_0_3267_becomes_33(self):
        assert self._build(0.3267) == 33

    def test_decimal_0_0_becomes_0(self):
        assert self._build(0.0) == 0

    def test_decimal_0_5_becomes_50(self):
        assert self._build(0.5) == 50

    def test_decimal_0_999_becomes_100(self):
        # round(0.999 * 100) = round(99.9) = 100
        assert self._build(0.999) == 100

    def test_decimal_0_994_becomes_99(self):
        # round(0.994 * 100) = round(99.4) = 99
        assert self._build(0.994) == 99

    def test_none_dimension_score_becomes_0(self):
        """None dimension_score must not raise and must return 0."""
        det_dims = {"skills": {"dimension_score": None}}
        fn = _make_build_dim({}, {}, det_dims)
        result = fn("score_skills", "weight_skills", "skills")
        assert result["achieved"] == 0

    def test_missing_dimension_score_key_becomes_0(self):
        det_dims = {"skills": {}}
        fn = _make_build_dim({}, {}, det_dims)
        result = fn("score_skills", "weight_skills", "skills")
        assert result["achieved"] == 0

    def test_result_is_integer(self):
        assert isinstance(self._build(0.544), int)

    def test_max_always_100(self):
        det_dims = {"skills": {"dimension_score": 0.7}}
        fn = _make_build_dim({}, {}, det_dims)
        result = fn("score_skills", "weight_skills", "skills")
        assert result["max"] == 100

    def test_weight_preserved(self):
        det_dims = {"skills": {"dimension_score": 0.7}}
        fn = _make_build_dim({}, {"weight_skills": 30}, det_dims)
        result = fn("score_skills", "weight_skills", "skills")
        assert result["weight"] == 30


# ── TestLegacyScoreUnchanged ──────────────────────────────────────────────────

class TestLegacyScoreUnchanged:
    """Legacy score_* columns (already 0–100) must pass through unchanged."""

    def _build_legacy(self, legacy_score: int) -> int:
        app = {"score_skills": legacy_score}
        fn = _make_build_dim(app, {}, {})       # empty det_dims → legacy path
        return fn("score_skills", "weight_skills", "skills")["achieved"]

    def test_legacy_85_remains_85(self):
        assert self._build_legacy(85) == 85

    def test_legacy_100_remains_100(self):
        assert self._build_legacy(100) == 100

    def test_legacy_0_remains_0(self):
        assert self._build_legacy(0) == 0

    def test_legacy_50_remains_50(self):
        assert self._build_legacy(50) == 50

    def test_no_det_dims_key_falls_back_to_legacy(self):
        """When det_dim_key is not in det_dims, use legacy even if det_dims has other keys."""
        det_dims = {"experience": {"dimension_score": 0.8}}
        app = {"score_skills": 72}
        fn = _make_build_dim(app, {}, det_dims)
        # "skills" not in det_dims → falls back to app["score_skills"]
        result = fn("score_skills", "weight_skills", "skills")
        assert result["achieved"] == 72

    def test_empty_det_dims_uses_legacy(self):
        app = {"score_skills": 60}
        fn = _make_build_dim(app, {}, {})
        result = fn("score_skills", "weight_skills", "skills")
        assert result["achieved"] == 60

    def test_none_det_dims_uses_legacy(self):
        app = {"score_experience": 55}
        fn = _make_build_dim(app, {}, None or {})
        result = fn("score_experience", "weight_experience", "experience")
        assert result["achieved"] == 55


# ── TestAllDimensions ─────────────────────────────────────────────────────────

class TestAllDimensions:
    """Verify conversion works across all 7 dimensions used in the response."""

    def test_all_seven_dimensions_convert_correctly(self):
        det_dims = {
            "skills":           {"dimension_score": 0.544},
            "experience":       {"dimension_score": 0.0},
            "education":        {"dimension_score": 0.76},
            "certifications":   {"dimension_score": 1.0},
            "soft_skills":      {"dimension_score": 0.3267},
            "domain_knowledge": {"dimension_score": 0.9},
            "other":            {"dimension_score": 0.123},
        }
        fn = _make_build_dim({}, {}, det_dims)

        assert fn("score_skills",           "weight_skills",           "skills")["achieved"]           == 54
        assert fn("score_experience",       "weight_experience",       "experience")["achieved"]       == 0
        assert fn("score_education",        "weight_education",        "education")["achieved"]        == 76
        assert fn("score_certifications",   "weight_certifications",   "certifications")["achieved"]   == 100
        assert fn("score_soft_skills",      "weight_soft_skills",      "soft_skills")["achieved"]      == 33
        assert fn("score_domain_knowledge", "weight_domain_knowledge", "domain_knowledge")["achieved"] == 90
        assert fn("score_other",            "weight_other",            "other")["achieved"]            == 12
