# EasyCAD app (SPEC12, hosted mode).
#
# The HTTP/API server + built frontend. In hosted mode it delegates CadQuery
# execution to the worker container (EASYCAD_WORKER_URL), so it does NOT need
# CadQuery/OCP here — keeping this image small. Execution isolation lives in
# `worker/Dockerfile`.
#
FROM python:3.11-slim

# Build id baked into the image (SPEC21 W1) — CI passes the git tag/SHA. The
# version IS the built image, not a property of the deploy host; a runtime
# EASYCAD_VERSION env still overrides it.
ARG EASYCAD_VERSION=unknown
ENV EASYCAD_VERSION=$EASYCAD_VERSION

WORKDIR /app

# App-only deps (no cadquery — execution is delegated to the worker).
RUN pip install --no-cache-dir \
        fastapi "uvicorn[standard]" pydantic openai python-dotenv

# Backend + prebuilt frontend. `static/` must be built first (`npm run build`);
# CI does this before the image build.
COPY app ./app
COPY static ./static

# Trusted tier (no untrusted-code execution here — that's the worker). Runs as
# root so it can write the mounted /data volume (the accounts DB, SPEC13 — CAD
# sessions are in-memory). Isolation of LLM-generated code lives in worker/Dockerfile.
EXPOSE 8852
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8852"]
