# EasyCAD CadQuery worker (SPEC12).
#
# Build context is the REPO ROOT (so the shared execution core
# `app/cq_worker.py` can be vendored in), but this image imports nothing from
# the `app` package at runtime — cq_worker is copied in as a top-level module.
#
#   docker build -f worker/Dockerfile -t easycad-worker .
#
FROM python:3.11-slim

# Runtime libs OCP/CadQuery links against (headless). Adjust if a future
# CadQuery/OCP build pulls in more.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglu1-mesa libxext6 libxrender1 libsm6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY worker/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Shared code — single source of truth with local mode (execution core + guard).
COPY app/cq_worker.py ./cq_worker.py
COPY app/code_guard.py ./code_guard.py
# Worker service code.
COPY worker/main.py worker/limits.py ./

# Non-root, matching the compose `user:` and a fixed uid for tmpfs ownership.
RUN useradd --uid 10001 --create-home appuser
USER 10001:10001

EXPOSE 8853
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8853"]
