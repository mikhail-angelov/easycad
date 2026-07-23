# EasyCAD app (SPEC12, hosted mode).
#
# The HTTP/API server + built frontend. In hosted mode it delegates CadQuery
# execution to the worker container (EASYCAD_WORKER_URL), so it does NOT need
# CadQuery/OCP here — keeping this image small. Execution isolation lives in
# `worker/Dockerfile`.
#
FROM python:3.11-slim

WORKDIR /app

# App-only deps (no cadquery — execution is delegated to the worker).
RUN pip install --no-cache-dir \
        fastapi "uvicorn[standard]" pydantic openai python-dotenv

# Backend + prebuilt frontend. `static/` must be built first (`npm run build`);
# CI does this before the image build.
COPY app ./app
COPY static ./static

# Trusted tier (no untrusted-code execution here — that's the worker). Runs as
# root so it can write the mounted /data volume (session autosave). Isolation
# of LLM-generated code lives entirely in worker/Dockerfile.
EXPOSE 8852
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8852"]
