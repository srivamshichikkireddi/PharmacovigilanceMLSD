FROM python:3.11-slim
# poppler-utils: clean text extraction from the Gazette PDF (pypdf inserts stray spaces in words)
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
COPY requirements.txt requirements-embed.txt ./
RUN pip install --no-cache-dir -r requirements.txt
ARG WITH_EMBEDDINGS=0
# CPU-only torch keeps the image ~1 GB smaller than the default CUDA build
RUN if [ "$WITH_EMBEDDINGS" = "1" ]; then \
      pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
      pip install --no-cache-dir -r requirements-embed.txt; fi
COPY pv ./pv
COPY prompts ./prompts
COPY corpus ./corpus
COPY eval ./eval
COPY tests ./tests
COPY contracts ./contracts
RUN useradd -m app && mkdir -p /data && chown -R app /data /app
USER app
CMD ["uvicorn", "pv.api:app", "--host", "0.0.0.0", "--port", "8000"]
