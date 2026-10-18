"""Structural checks on .github/workflows/ci.yml that run without GitHub.

A workflow with an invalid expression is rejected before any job starts, so the run shows failure with zero jobs. The checks here
catch the defects that cause that: context names that a job cannot read, unpinned actions, references to missing jobs, and secrets.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"

# Contexts GitHub makes available to jobs.<id>.env (and to the other job-level keys). `runner`, `env` and `steps` are not among them.
JOB_LEVEL_CONTEXTS = {"github", "needs", "strategy", "matrix", "vars", "inputs"}
EXPRESSION = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")
CONTEXT_NAME = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\.[a-zA-Z_]")
SHA_PIN = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


@pytest.fixture(scope="module")
def workflow() -> dict:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _on(workflow: dict) -> dict:
    # YAML 1.1 reads the bare key `on` as the boolean True.
    return workflow.get("on", workflow.get(True))


def _expressions(value) -> list[str]:
    found: list[str] = []
    if isinstance(value, str):
        found.extend(EXPRESSION.findall(value))
    elif isinstance(value, dict):
        for item in value.values():
            found.extend(_expressions(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_expressions(item))
    return found


def test_the_workflow_parses_and_declares_triggers_and_read_only_permissions(workflow):
    assert _on(workflow), "the workflow declares no trigger"
    assert workflow.get("permissions") == {"contents": "read"}


def test_every_job_has_a_runner_and_a_timeout(workflow):
    jobs = workflow["jobs"]
    assert jobs, "no jobs"
    for name, job in jobs.items():
        assert job.get("runs-on"), f"{name} has no runs-on"
        assert isinstance(job.get("timeout-minutes"), int), f"{name} has no timeout-minutes"


def test_job_level_expressions_use_only_contexts_available_to_jobs(workflow):
    """Regression: `runner.temp` in jobs.<id>.env made GitHub reject the whole workflow (zero jobs, failure)."""
    for name, job in workflow["jobs"].items():
        job_level = {k: v for k, v in job.items() if k != "steps"}
        for expression in _expressions(job_level):
            for context in CONTEXT_NAME.findall(expression):
                assert context in JOB_LEVEL_CONTEXTS, (
                    f"{name} uses ${{{{ {expression} }}}} at job level; the {context!r} context is not available there")


def test_steps_do_not_reference_runner_paths_through_job_env(workflow):
    for name, job in workflow["jobs"].items():
        assert "runner" not in (job.get("env") or {}) and not any(
            "runner." in str(v) for v in (job.get("env") or {}).values()), f"{name} reads runner context in job env"


def test_every_action_reference_is_pinned_to_a_full_commit_sha(workflow):
    for name, job in workflow["jobs"].items():
        for step in job["steps"]:
            uses = step.get("uses")
            if uses:
                assert SHA_PIN.match(uses), f"{name}: {uses} is not pinned to a 40-character commit SHA"


def test_needs_refer_to_jobs_that_exist(workflow):
    jobs = set(workflow["jobs"])
    for name, job in workflow["jobs"].items():
        needs = job.get("needs", [])
        needs = [needs] if isinstance(needs, str) else needs
        assert set(needs) <= jobs, f"{name} needs an unknown job"


def test_the_workflow_references_no_secrets(workflow):
    assert not any(expr.startswith("secrets") or "secrets." in expr
                   for expr in _expressions(workflow)), "the CI workflow must run without secrets"


def test_every_job_has_a_collection_or_result_gate(workflow):
    """A job that runs nothing and reports success would make CI look green. Each job must run at least one step."""
    for name, job in workflow["jobs"].items():
        assert any(step.get("run") or step.get("uses") for step in job["steps"]), f"{name} runs no steps"
