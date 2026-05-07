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

# ── Pre-download embedding model ──────────────────────────────────────────────
# Downloads paraphrase-multilingual-MiniLM-L12-v2 (~120MB) into the image
# so workers start instantly without a network download on first use.
# Model is cached at /root/.cache/huggingface/hub/
RUN python -c "\
from sentence_transformers import SentenceTransformer; \
SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2'); \
print('Embedding model downloaded successfully.')"

# ── Application code ──────────────────────────────────────────────────────────
COPY . .

# Create file storage directory
RUN mkdir -p /files

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
