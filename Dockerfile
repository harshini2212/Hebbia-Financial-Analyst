# Container for the full live app (FastAPI UI + streaming API + synthetic engine).
# Cloud Run:  gcloud run deploy tieout --source . --region us-central1 \
#               --allow-unauthenticated --port 8080 --timeout 3600 --memory 2Gi
# (Render uses render.yaml instead — `python serve.py`; this Dockerfile is the
#  Cloud Run / any-container path.)
FROM python:3.11-slim
WORKDIR /app

# arelle + lxml + numpy/scipy ship manylinux wheels, so no compiler is normally
# needed; uncomment if a source build is ever required.
# RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
#     && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# src/ layout (same as serve.py); RUN_PACE drips the stream so the hosted demo
# feels live (set 0 to run at full speed).
ENV PYTHONPATH=/app/src PORT=8080 RUN_PACE=0.25 TIEOUT_RUN_LIMIT=40
EXPOSE 8080
CMD ["sh", "-c", "uvicorn tieout.web.app:app --host 0.0.0.0 --port ${PORT}"]
