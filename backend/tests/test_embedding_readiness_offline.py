"""Production embedding-model readiness.

Production loads the sentence-transformer from a local directory and never downloads it during a
request. These tests prove that a missing or unprepared model fails clearly at startup, that the
readiness endpoint reports it without leaking the path, and that test doubles stay out of
production modules. No real model or network is used: the sentence-transformers module is faked.
"""
from __future__ import annotations

import asyncio
import pathlib
import re
import sys
import types

import pytest

import config
from services import local_processor
from services.local_processor import EmbeddingModelUnavailable

BACKEND = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _fresh_singleton(monkeypatch):
    monkeypatch.setattr(local_processor, "_MODEL", None)


def _settings(monkeypatch, **overrides):
    settings = config.Settings(_env_file=None, **overrides)
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    return settings


class _FakeSentenceTransformer:
    calls: list[str] = []
    fail_with: Exception | None = None

    def __init__(self, source):
        type(self).calls.append(source)
        if type(self).fail_with is not None:
            raise type(self).fail_with


@pytest.fixture
def fake_st(monkeypatch):
    _FakeSentenceTransformer.calls = []
    _FakeSentenceTransformer.fail_with = None
    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = _FakeSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    return _FakeSentenceTransformer


class TestLocalOnlyLoading:
    def test_unset_path_fails_clearly_and_never_downloads(self, monkeypatch, fake_st):
        _settings(monkeypatch, embedding_model_path="", embedding_model_local_only=True)
        with pytest.raises(EmbeddingModelUnavailable, match="EMBEDDING_MODEL_PATH is not set"):
            local_processor.ensure_model_loaded()
        assert fake_st.calls == []

    def test_missing_directory_fails_before_the_library_is_touched(self, monkeypatch, fake_st, tmp_path):
        missing = tmp_path / "not-prepared"
        _settings(monkeypatch, embedding_model_path=str(missing), embedding_model_local_only=True)
        with pytest.raises(EmbeddingModelUnavailable, match="is not a directory"):
            local_processor.ensure_model_loaded()
        assert fake_st.calls == []

    def test_prepared_directory_is_loaded_from_that_path(self, monkeypatch, fake_st, tmp_path):
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        _settings(monkeypatch, embedding_model_path=str(model_dir), embedding_model_local_only=True)
        local_processor.ensure_model_loaded()
        local_processor.ensure_model_loaded()  # cached: loaded once per process
        assert fake_st.calls == [str(model_dir)]

    def test_missing_library_is_reported_as_unavailable(self, monkeypatch, tmp_path):
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        _settings(monkeypatch, embedding_model_path=str(model_dir), embedding_model_local_only=True)
        monkeypatch.setitem(sys.modules, "sentence_transformers", None)  # import raises ImportError
        with pytest.raises(EmbeddingModelUnavailable, match="not installed"):
            local_processor.ensure_model_loaded()

    def test_corrupt_model_is_reported_with_the_cause(self, monkeypatch, fake_st, tmp_path):
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        fake_st.fail_with = OSError("config.json missing")
        _settings(monkeypatch, embedding_model_path=str(model_dir), embedding_model_local_only=True)
        with pytest.raises(EmbeddingModelUnavailable, match="config.json missing"):
            local_processor.ensure_model_loaded()

    def test_development_mode_may_use_the_model_name(self, monkeypatch, fake_st):
        settings = _settings(monkeypatch, embedding_model_path="", embedding_model_local_only=False)
        local_processor.ensure_model_loaded()
        assert fake_st.calls == [settings.embedding_model]


class TestReadiness:
    def test_unavailable_model_is_not_ready_and_the_response_has_no_path(self, monkeypatch, fake_st, tmp_path):
        secret_path = str(tmp_path / "private-location")
        _settings(monkeypatch, embedding_model_path=secret_path, embedding_model_local_only=True)
        check = local_processor.embedding_readiness()
        assert check == {"component": "embedding_model", "ready": False}
        assert secret_path not in str(check)

    def test_prepared_model_is_ready(self, monkeypatch, fake_st, tmp_path):
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        _settings(monkeypatch, embedding_model_path=str(model_dir), embedding_model_local_only=True)
        assert local_processor.embedding_readiness() == {"component": "embedding_model", "ready": True}

    def test_health_ready_returns_503_when_the_model_is_missing(self, monkeypatch, fake_st, tmp_path):
        import main

        _settings(monkeypatch, embedding_model_path=str(tmp_path / "absent"), embedding_model_local_only=True)
        response = asyncio.run(main.health_ready())
        assert response.status_code == 503
        assert b"unavailable" in response.body

    def test_lifespan_refuses_to_start_without_the_model(self, monkeypatch, fake_st, tmp_path):
        import main

        _settings(monkeypatch, embedding_model_path="", embedding_model_local_only=True)
        # The runtime-role check needs a database; this test is about the model check, so that check is stubbed out here.
        async def no_database_check(session):
            return None

        monkeypatch.setattr(main, "assert_runtime_role", no_database_check)
        monkeypatch.setattr(main, "AsyncSessionLocal", lambda: _NullSession())
        monkeypatch.setattr(main, "AdminSessionLocal", lambda: _NullSession())

        async def start():
            async with main.lifespan(main.app):
                pass

        with pytest.raises(EmbeddingModelUnavailable, match="EMBEDDING_MODEL_PATH is not set"):
            asyncio.run(start())


class _NullSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class TestSettings:
    def test_production_defaults_are_local_only_with_no_download_path(self):
        defaults = config.Settings(_env_file=None)
        assert defaults.embedding_model_local_only is True
        assert defaults.embedding_model_path == ""

    def test_environment_variables_configure_the_model_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("EMBEDDING_MODEL_PATH", str(tmp_path))
        monkeypatch.setenv("EMBEDDING_MODEL_LOCAL_ONLY", "true")
        assert config.Settings(_env_file=None).embedding_model_path == str(tmp_path)


class TestPinnedRuntime:
    REQUIRED = [
        "pymupdf", "sentence-transformers", "torch", "transformers", "huggingface-hub",
        "tokenizers", "safetensors", "scikit-learn", "scipy", "numpy", "rapidfuzz",
        "langdetect", "pyarabic",
    ]

    def test_embedding_and_pdf_runtime_packages_are_pinned_exactly(self):
        pins = {}
        for line in (BACKEND / "requirements.txt").read_text().splitlines():
            m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s#]+)", line.strip())
            if m:
                pins[m.group(1).lower()] = m.group(2)
        missing = [name for name in self.REQUIRED if name not in pins]
        assert missing == [], f"not pinned with == in requirements.txt: {missing}"


class TestTestDoublesStayOutOfProduction:
    FORBIDDEN = re.compile(r"DeterministicEncoder|FakeAIClient|_fake_score_cv|_no_email")
    TEST_LOCATIONS = ("tests", "tests/acceptance")

    def test_no_production_module_references_a_test_double(self):
        hits = []
        for path in BACKEND.rglob("*.py"):
            rel = path.relative_to(BACKEND)
            if rel.parts[0] == "tests" or "__pycache__" in rel.parts or rel.parts[0] == "prototypes":
                continue
            if self.FORBIDDEN.search(path.read_text(encoding="utf-8", errors="replace")):
                hits.append(str(rel))
        assert hits == [], f"test doubles referenced from production code: {hits}"
