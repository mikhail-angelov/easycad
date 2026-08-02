# EasyCAD CadQuery worker (SPEC12).
#
# Build context is the REPO ROOT (so the shared execution core
# `app/cq_worker.py` can be vendored in), but this image imports nothing from
# the `app` package at runtime — cq_worker is copied in as a top-level module.
#
#   docker build -f worker/Dockerfile -t easycad-worker .
#
FROM python:3.11-slim

# Build id baked into the image (SPEC21 W1) — same tag/SHA as the app image, so
# a worker log line reports the running build. Keeps the worker secret-free and
# volume-free (version comes from this ENV, not a mount).
ARG EASYCAD_VERSION=unknown
ENV EASYCAD_VERSION=$EASYCAD_VERSION

# Runtime libs OCP/CadQuery links against (headless). Adjust if a future
# CadQuery/OCP build pulls in more.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglu1-mesa libxext6 libxrender1 libsm6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY worker/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Shared code — single source of truth with local mode (execution core + guard).
# NOTE: these app/* files are worker image inputs. If you add/remove one here,
# update the `worker:` path list in .github/workflows/ci.yml (the change-detection
# filter that decides whether to rebuild this image), or detection will drift.
COPY app/cq_worker.py ./cq_worker.py
COPY app/code_guard.py ./code_guard.py
# Worker service code.
COPY worker/main.py worker/limits.py worker/zygote.py worker/log_context.py ./

# Non-root, matching the compose `user:` and a fixed uid for tmpfs ownership.
RUN useradd --uid 10001 --create-home appuser
USER 10001:10001

EXPOSE 8853
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8853"]
