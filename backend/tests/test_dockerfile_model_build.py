"""The image build downloads the embedding model with Hugging Face traffic allowed, and the final image is offline.

Fails if the download step runs with offline mode on, if it does not pass local_files_only=False, or if the final image does not set
HF_HUB_OFFLINE=1, EMBEDDING_MODEL_PATH and EMBEDDING_MODEL_LOCAL_ONLY=true.
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"
MODEL_PATH = "/opt/models/paraphrase-multilingual-MiniLM-L12-v2"
MODEL_STEP = re.compile(r"SentenceTransformer\(")


def _instructions(text: str) -> list[tuple[str, str]]:
    """(keyword, arguments) for each instruction, with line continuations joined and comments dropped."""
    joined: list[str] = []
    pending = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if not pending and (not line.strip() or line.lstrip().startswith("#")):
            continue
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        joined.append(pending + line)
        pending = ""
    result = []
    for item in joined:
        keyword, _, args = item.strip().partition(" ")
        result.append((keyword.upper(), args.strip()))
    return result


def _env_pairs(args: str) -> dict[str, str]:
    return {k: v for k, v in (token.split("=", 1) for token in shlex.split(args) if "=" in token)}


@pytest.fixture(scope="module")
def steps():
    return _instructions(DOCKERFILE.read_text(encoding="utf-8"))


def _model_download_step(steps):
    matches = [(i, args) for i, (kw, args) in enumerate(steps) if kw == "RUN" and MODEL_STEP.search(args)]
    assert len(matches) == 1, "expected exactly one model download step"
    return matches[0]


def test_the_model_download_step_runs_with_hugging_face_traffic_allowed(steps):
    index, args = _model_download_step(steps)
    env_before = {}
    for kw, a in steps[:index]:
        if kw == "ENV":
            env_before.update(_env_pairs(a))
    inline = _env_pairs(args.split("python", 1)[0])
    effective = inline.get("HF_HUB_OFFLINE", env_before.get("HF_HUB_OFFLINE", "0"))
    assert effective == "0", f"the download step runs with HF_HUB_OFFLINE={effective}; it must be 0"
    assert inline.get("HF_HUB_OFFLINE") == "0", "the download step must state HF_HUB_OFFLINE=0 itself"


def test_the_download_step_does_not_use_local_files_only(steps):
    _, args = _model_download_step(steps)
    assert "local_files_only=False" in args
    assert "local_files_only=True" not in args


def test_offline_mode_is_enabled_only_after_the_model_is_saved(steps):
    index, _ = _model_download_step(steps)
    enabling = [i for i, (kw, a) in enumerate(steps) if kw == "ENV" and _env_pairs(a).get("HF_HUB_OFFLINE") == "1"]
    assert enabling, "the final image must set HF_HUB_OFFLINE=1"
    assert all(i > index for i in enabling), "HF_HUB_OFFLINE=1 must not be set before the download step"


def test_the_final_image_is_offline_and_reads_the_model_from_disk(steps):
    final: dict[str, str] = {}
    for kw, args in steps:
        if kw == "ENV":
            final.update(_env_pairs(args))
    assert final.get("HF_HUB_OFFLINE") == "1"
    assert final.get("EMBEDDING_MODEL_PATH") == MODEL_PATH
    assert final.get("EMBEDDING_MODEL_LOCAL_ONLY") == "true"
