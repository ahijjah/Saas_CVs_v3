"""Deterministic check of the production embedding runtime.

Loads the sentence-transformer through the production loader (services.local_processor), with no test doubles, and
runs one real similarity computation. It never downloads: it sets HF_HUB_OFFLINE before any model library import and
requires EMBEDDING_MODEL_PATH to name an existing local directory.

    EMBEDDING_MODEL_PATH=/opt/models/paraphrase-multilingual-MiniLM-L12-v2 \\
    EMBEDDING_MODEL_LOCAL_ONLY=true python3 scripts/verify_embedding_runtime.py

Exit status 0 means the real model loaded and produced a valid, ordered similarity. Anything else exits 1 with the reason on
stderr. The script prints library versions so the deployment record can show what was verified.
"""
from __future__ import annotations

import math
import os
import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

os.environ["HF_HUB_OFFLINE"] = "1"

SIMILAR_CV = "Python backend engineer with five years building REST APIs and PostgreSQL schemas."
SIMILAR_JOB = "Senior Python developer to build REST APIs and design PostgreSQL databases."
UNRELATED_JOB = "Experienced pastry chef required for a busy bakery specialising in sourdough and croissants."


def _fail(message: str) -> int:
    print(f"FAIL: {message}", file=sys.stderr)
    return 1


def _versions() -> dict[str, str]:
    import fitz
    import sentence_transformers
    import torch
    import transformers

    return {
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "sentence-transformers": sentence_transformers.__version__,
        "pymupdf": fitz.VersionBind,
    }


def main() -> int:
    path = os.environ.get("EMBEDDING_MODEL_PATH", "").strip()
    if not path:
        return _fail("EMBEDDING_MODEL_PATH is not set")
    if not pathlib.Path(path).is_dir():
        return _fail(f"EMBEDDING_MODEL_PATH '{path}' is not a directory")

    try:
        versions = _versions()
    except ImportError as exc:
        return _fail(f"runtime package missing: {exc}")
    for name, version in versions.items():
        print(f"{name}=={version}")

    from services import local_processor
    from services.local_processor import EmbeddingModelUnavailable

    try:
        local_processor.ensure_model_loaded()
    except EmbeddingModelUnavailable as exc:
        return _fail(str(exc))

    similar = local_processor.compute_semantic_similarity(SIMILAR_CV, SIMILAR_JOB)
    unrelated = local_processor.compute_semantic_similarity(SIMILAR_CV, UNRELATED_JOB)
    print(f"similarity similar={similar:.4f} unrelated={unrelated:.4f}")

    for label, value in (("similar", similar), ("unrelated", unrelated)):
        if not isinstance(value, float) or not math.isfinite(value) or not 0.0 <= value <= 1.0:
            return _fail(f"{label} similarity is not a finite float in [0, 1]: {value!r}")
    if not similar > unrelated:
        return _fail(f"similar pair ({similar:.4f}) does not score above unrelated pair ({unrelated:.4f})")

    print("OK: real embedding model loaded locally and produced ordered similarities")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
