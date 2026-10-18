# Model artifacts

The production API and Celery workers use one sentence-transformer model for local similarity scoring. Its weights are not part of
this repository. Model weights are never committed to git, and no release commit may contain them.

## Required model

| Field | Value |
|---|---|
| Name | `paraphrase-multilingual-MiniLM-L12-v2` (Hugging Face: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`) |
| Library | `sentence-transformers==3.3.1` (pinned in `backend/requirements.txt`) |
| Revision (commit SHA) | see "Verification status" below |
| Manifest SHA-256 | see "Verification status" below |
| Deployment location | `/opt/models/paraphrase-multilingual-MiniLM-L12-v2` (the path `backend/Dockerfile` saves to and sets as `EMBEDDING_MODEL_PATH`) |

## How the directory is produced

The image build saves a local copy. It does not fetch the model at runtime:

```bash
python -c "from sentence_transformers import SentenceTransformer; \
SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2').save('/opt/models/paraphrase-multilingual-MiniLM-L12-v2')"
```

The build host needs network access once. Every API and worker process then runs with `EMBEDDING_MODEL_LOCAL_ONLY=true` and
`HF_HUB_OFFLINE=1`, and reads only that directory.

To prepare a host without rebuilding the image, produce the same directory on a connected build machine, then copy it to the
host.

## Integrity: SHA-256 manifest

Record a manifest of every file in the directory, sorted by path, so the deployment can prove it has the approved bytes:

```bash
cd /opt/models/paraphrase-multilingual-MiniLM-L12-v2
find . -type f -print0 | sort -z | xargs -0 sha256sum > /tmp/model.manifest
sha256sum /tmp/model.manifest
```

Store the manifest and its digest with the release record. On a new host, repeat the command and compare the digest. A mismatch
means the directory is not the approved model and must not be used.

## Mounting

Mount the directory read-only into the API and worker containers. A read-only mount prevents the runtime from writing into it:

```yaml
volumes:
  - /opt/models/paraphrase-multilingual-MiniLM-L12-v2:/opt/models/paraphrase-multilingual-MiniLM-L12-v2:ro
```

## Verifying a host

Run the deterministic check from `backend/` with the same environment the service uses:

```bash
EMBEDDING_MODEL_PATH=/opt/models/paraphrase-multilingual-MiniLM-L12-v2 \
EMBEDDING_MODEL_LOCAL_ONLY=true \
python3 scripts/verify_embedding_runtime.py
```

It loads the model through the production loader, performs one real similarity computation, and exits 0 only when the score is a
finite value in [0, 1] and the similar pair scores above the unrelated pair. It prints the library versions it used. It downloads
nothing. A missing, non-directory or corrupt path exits 1 with the reason.

Readiness is also visible at runtime: `GET /health/ready` returns 200 only when the model loaded.

## Verification status

- Not verified in this change. The approved model host (`huggingface.co`) was unreachable from the build environment
  (HTTP 000 on every check), so the model could not be downloaded, its revision recorded, or its manifest computed.
- The deterministic verification script and the failure-path checks are in the repository and are the repository-level check.
- Deployment prerequisite: a connected build host must obtain the model, record its revision and manifest digest here, and run
  `scripts/verify_embedding_runtime.py` on every host before the release is promoted.
