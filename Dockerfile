FROM python:3.11-slim

WORKDIR /app

# tesseract-ocr powers OCR for scanned PDFs and image uploads; without it the
# app still runs, but those ingestion paths degrade to "no text extracted".
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py database.py config.py token_logger.py seed_registry.py llm_client.py logging_config.py ./
COPY phase1_upload/ ./phase1_upload/
COPY phase2_retrieval/ ./phase2_retrieval/
COPY phase3_formatting/ ./phase3_formatting/
COPY phase4_integrations/ ./phase4_integrations/

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["gunicorn", "-w", "4", "-k", "gthread", "--threads", "4", "--timeout", "120", "--bind", "0.0.0.0:8000", "server:app"]
