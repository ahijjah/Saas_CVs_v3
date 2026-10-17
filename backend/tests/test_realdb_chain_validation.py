"""Validation of the migration chain: what counts as the expected outcome, and that a disposable cluster is always stopped."""
from __future__ import annotations

import pathlib

import pytest

import realdb_helper as H
from realdb_helper import EXPECTED_PRE_EXISTING_FAILURES, ChainValidationError, StepFailure, check_chain

KNOWN = [StepFailure(name, "pre-existing") for name in sorted(EXPECTED_PRE_EXISTING_FAILURES)]


def test_the_exact_known_set_is_accepted():
    check_chain(KNOWN)


def test_a_simulated_unexpected_migration_failure_fails_the_chain_validation():
    with pytest.raises(ChainValidationError, match="unexpected failures"):
        check_chain(KNOWN + [StepFailure("050_new_feature.sql", "relation does not exist")])


def test_a_known_failure_that_now_succeeds_fails_the_chain_validation():
    # The chain currently has no expected failures, so this case is built from an explicit set rather than the constant.
    expected = frozenset({"010_platform_control.sql", "087_autosave_deterministic_answers.sql"})
    with pytest.raises(ChainValidationError, match="no longer fail"):
        check_chain([StepFailure("010_platform_control.sql", "trigger already exists")], expected=expected)


def test_a_failure_of_schema_sql_fails_the_chain_validation():
    with pytest.raises(ChainValidationError, match="schema.sql failed"):
        check_chain(KNOWN + [StepFailure("schema.sql", "users does not exist")])


def test_a_failure_of_migration_093_fails_the_chain_validation():
    with pytest.raises(ChainValidationError, match="093_requirements_v2_storage_schema.sql"):
        check_chain(KNOWN + [StepFailure("093_requirements_v2_storage_schema.sql", "column already exists")])


def test_failures_are_structured_records_not_strings():
    record = StepFailure("010_platform_control.sql", "trigger already exists")
    assert record.file == "010_platform_control.sql" and record.error == "trigger already exists"


def test_the_expected_set_names_real_migration_files():
    names = {p.name for p in H.migration_files()}
    assert EXPECTED_PRE_EXISTING_FAILURES <= names


def test_a_disposable_cluster_is_stopped_even_when_construction_fails(monkeypatch):
    if H.unavailable_reason():
        pytest.skip(H.unavailable_reason())
    stopped: list[pathlib.Path] = []
    original_stop = H.Cluster.stop

    def recording_stop(self):
        stopped.append(self.dir)
        original_stop(self)

    def failing_build(cluster):
        raise RuntimeError("simulated construction failure")

    monkeypatch.setattr(H.Cluster, "stop", recording_stop)
    monkeypatch.setattr(H, "build", failing_build)
    with pytest.raises(RuntimeError, match="simulated construction failure"):
        with H.built_chain():
            pass
    assert len(stopped) == 1
    assert not stopped[0].exists(), "the cluster directory must be removed"
