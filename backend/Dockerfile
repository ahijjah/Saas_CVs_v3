FROM python:3.11-slim

# Install LibreOffice headless for DOCX→PDF conversion
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create file storage directory
RUN mkdir -p /files

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
