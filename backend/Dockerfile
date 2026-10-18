FROM python:3.11-slim

# ── System dependencies ───────────────────────────────────────────────────────
# LibreOffice headless: DOCX→PDF conversion
# libgomp1: required by sentence-transformers / PyTorch CPU
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice \
    libgl1 \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONPATH=/app

# ── Python dependencies ───────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Embedding model (baked into the image, loaded from disk at runtime) ─────
# The build downloads paraphrase-multilingual-MiniLM-L12-v2 once and saves a local copy. Hugging Face traffic is allowed for this
# step only: HF_HUB_OFFLINE is 0 here and HF_HUB_OFFLINE=1 is set after the save. At runtime the app reads only that directory and
# never reaches the network for it.
ENV EMBEDDING_MODEL_PATH=/opt/models/paraphrase-multilingual-MiniLM-L12-v2 \
    EMBEDDING_MODEL_LOCAL_ONLY=true
RUN HF_HUB_OFFLINE=0 python -c "\
from sentence_transformers import SentenceTransformer; \
SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2', local_files_only=False).save('/opt/models/paraphrase-multilingual-MiniLM-L12-v2'); \
print('Embedding model saved to /opt/models.')"
ENV HF_HUB_OFFLINE=1

# ── Application code ──────────────────────────────────────────────────────────
COPY . .

# Create file storage directory
RUN mkdir -p /files

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
