"""Release mutation checks.

Each mutant is applied to a throw-away copy of the repository, then the test that must catch it is
run. A mutant is "caught" when that test exits non-zero. The script exits 1 if any mutant survives.
The repository itself is never modified.

    python3 scripts/release_mutation_checks.py            # all mutants
    python3 scripts/release_mutation_checks.py --only ts-config

Requires: python3, pytest (backend), node/npx with node_modules installed (frontend mutants).
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parents[1]
E2E_CLEANUP_CHECK = ROOT / "e2e" / "mutation_cleanup_check.sh"


@dataclass(frozen=True)
class Mutant:
    key: str
    description: str
    file: str            # path relative to the repository root
    old: str             # text to replace (must occur exactly once)
    new: str
    command: list[str]   # run inside the copy; must exit non-zero when the mutant is caught
    surface: str         # "backend" or "frontend"
    extra_files: tuple[str, ...] = ()   # added to the copy before the mutation (rare)


MUTANTS: tuple[Mutant, ...] = (
    Mutant(
        key="event-loop-leak",
        description="Issue #20: security tests use get_event_loop() again",
        file="backend/tests/test_security_detection.py",
        old="    return asyncio.run(coro)",
        new="    return asyncio.get_event_loop().run_until_complete(coro)",
        command=["python3", "-m", "pytest", "tests/test_criteria_status_postgres.py",
                 "tests/test_security_detection.py", "-q", "-p", "no:cacheprovider"],
        surface="backend",
    ),
    Mutant(
        key="security-test-skipped",
        description="Issue #20: one security test is marked skip",
        file="backend/tests/test_security_detection.py",
        old="    def test_clean_cv_passes(self):",
        new='    @pytest.mark.skip(reason="mutant")\n    def test_clean_cv_passes(self):',
        command=["python3", "-m", "pytest", "tests/test_security_detection.py::TestSuiteIntegrity",
                 "-q", "-p", "no:cacheprovider"],
        surface="backend",
    ),
    Mutant(
        key="security-test-removed",
        description="Issue #20: one security test is deleted",
        file="backend/tests/test_security_detection.py",
        old="    def test_summary_contains_no_raw_text(self):",
        new="    def _removed_summary_check(self):",
        command=["python3", "-m", "pytest", "tests/test_security_detection.py::TestSuiteIntegrity",
                 "-q", "-p", "no:cacheprovider"],
        surface="backend",
    ),
    Mutant(
        key="ts-config-exclude",
        description="TypeScript: configuration excludes a source file from the check",
        file="tsconfig.json",
        old='"noEmit": true\n  }\n}',
        new='"noEmit": true\n  },\n  "exclude": ["pages/JobDetails.tsx"]\n}',
        command=["npx", "vitest", "run", "tests/frontend/typecheckGuard.test.ts"],
        surface="frontend",
    ),
    Mutant(
        key="ts-config-not-strict-off",
        description="TypeScript: implicit any allowed by configuration",
        file="tsconfig.json",
        old='"noEmit": true\n  }\n}',
        new='"noEmit": true,\n    "noImplicitAny": false\n  }\n}',
        command=["npx", "vitest", "run", "tests/frontend/typecheckGuard.test.ts"],
        surface="frontend",
    ),
    Mutant(
        key="ts-ignore-in-source",
        description="TypeScript: an error is suppressed with @ts-ignore in source",
        file="components/Toast.tsx",
        old="export type ToastType",
        new="// @ts-ignore\nexport type ToastType",
        command=["npx", "vitest", "run", "tests/frontend/typecheckGuard.test.ts"],
        surface="frontend",
    ),
    Mutant(
        key="deterministic-encoder-in-production",
        description="Production code returns a deterministic test encoder",
        file="backend/services/local_processor.py",
        old="def _get_model():\n    ensure_model_loaded()\n    return _MODEL",
        new="class DeterministicEncoder:\n    def encode(self, texts, normalize_embeddings=True):\n        return texts\n\n\ndef _get_model():\n    return DeterministicEncoder()",
        command=["python3", "-m", "pytest",
                 "tests/test_embedding_readiness_offline.py::TestTestDoublesStayOutOfProduction",
                 "-q", "-p", "no:cacheprovider"],
        surface="backend",
    ),
    Mutant(
        key="readiness-validation-skipped",
        description="API lifespan no longer validates the embedding model",
        file="backend/main.py",
        old="    local_processor.ensure_model_loaded()\n    yield",
        new="    yield",
        command=["python3", "-m", "pytest",
                 "tests/test_embedding_readiness_offline.py::TestReadiness::test_lifespan_refuses_to_start_without_the_model",
                 "-q", "-p", "no:cacheprovider"],
        surface="backend",
    ),
    Mutant(
        key="model-downloads-in-production",
        description="Loader ignores EMBEDDING_MODEL_LOCAL_ONLY and falls back to the model name",
        file="backend/services/local_processor.py",
        old="    if settings.embedding_model_local_only:",
        new="    if False:",
        command=["python3", "-m", "pytest",
                 "tests/test_embedding_readiness_offline.py::TestLocalOnlyLoading::test_unset_path_fails_clearly_and_never_downloads",
                 "-q", "-p", "no:cacheprovider"],
        surface="backend",
    ),
)


def _copy_repo(dest: pathlib.Path) -> None:
    def ignore(_dir: str, names: list[str]) -> set[str]:
        return {n for n in names if n in {"node_modules", ".git", "__pycache__", "dist", "artifacts"}}
    shutil.copytree(ROOT, dest, ignore=ignore, symlinks=False, dirs_exist_ok=False)
    nm = ROOT / "node_modules"
    if nm.exists():
        (dest / "node_modules").symlink_to(nm)


def _run(mutant: Mutant, work: pathlib.Path) -> tuple[bool, str]:
    target = work / mutant.file
    text = target.read_text(encoding="utf-8")
    count = text.count(mutant.old)
    if count != 1:
        return False, f"anchor found {count} times; mutant not applied"
    target.write_text(text.replace(mutant.old, mutant.new), encoding="utf-8")
    cwd = work / ("backend" if mutant.surface == "backend" else "")
    proc = subprocess.run(mutant.command, cwd=cwd, capture_output=True, text=True, timeout=900)
    caught = proc.returncode != 0
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-1:] or [""]
    return caught, tail[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="run mutants whose key contains this text")
    args = parser.parse_args(argv)

    selected = [m for m in MUTANTS if not args.only or args.only in m.key]
    if not selected:
        print("no mutants selected", file=sys.stderr)
        return 2

    failures = 0
    for mutant in selected:
        with tempfile.TemporaryDirectory(prefix="mutant-") as tmp:
            work = pathlib.Path(tmp) / "repo"
            _copy_repo(work)
            caught, detail = _run(mutant, work)
        verdict = "caught" if caught else "SURVIVED"
        failures += 0 if caught else 1
        print(f"{verdict:9} {mutant.key:<36} {mutant.description}")
        print(f"          {detail}")

    if E2E_CLEANUP_CHECK.exists():
        proc = subprocess.run(["bash", str(E2E_CLEANUP_CHECK)], cwd=ROOT, capture_output=True, text=True, timeout=900)
        verdict = "caught" if proc.returncode != 0 else "SURVIVED"
        failures += 0 if proc.returncode != 0 else 1
        print(f"{verdict:9} {'e2e-cleanup-leak':<36} E2E cleanup leaves backend/frontend/postgres running")
    else:
        print("MISSING   e2e-cleanup-leak                     e2e/mutation_cleanup_check.sh not present")
        failures += 1

    print(f"\n{len(selected) - failures}/{len(selected)} selected backend/frontend mutants caught")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
