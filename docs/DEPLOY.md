# Deploying EasyCAD (easycad.bconf.com)

Hosted deployment is the SPEC12 two-container split: a trusted **app** (HTTP,
LLM, user data) and an isolated **worker** that runs LLM-generated CadQuery code
with no secrets and no network egress. Local/desktop use needs none of this —
just `make run` (the app executes in-process when `EASYCAD_WORKER_URL` is unset).

## Pipeline

```
git push (main / v* tag)
  └─ CI (.github/workflows/ci.yml): pytest + frontend build
       └─ build & push two images to ghcr.io:
            ghcr.io/mikhail-angelov/easycad          (app, no CadQuery)
            ghcr.io/mikhail-angelov/easycad-worker   (worker, CadQuery/OCP)
make install   # one-time: seed /opt/easycad on the server
make deploy    # pull latest images, docker compose down/up
```

## Server prerequisites (one time)

1. **Docker + Compose** on the host.
2. **External proxy network** used by the reverse proxy:
   `docker network create proxy-net` (if not already present).
3. **`redoproxy`** reverse proxy running and attached to `proxy-net`. It reads
   the app container's labels and serves TLS for the domain:
   - `redoproxy.domain: easycad.bconf.com`
   - `redoproxy.port: 8852`
4. **DNS**: `easycad.bconf.com` → server IP.
5. **GHCR access**: the images are under `ghcr.io/mikhail-angelov/…`. If the
   packages are private, `docker login ghcr.io` on the server with a PAT
   (`read:packages`). Public packages need no login.

## First deploy

```sh
cp .env.prod.example .env.prod      # fill in DEEP_SEEK_KEY etc.
echo "HOST=<server-ip-or-host>" >> .env   # local .env — used by the Makefile
make install                        # scp .env.prod + compose to /opt/easycad
make deploy                         # pull images, up -d
```

`make install` also creates `/opt/easycad/data`, bind-mounted at `/data` for the
accounts DB (`EASYCAD_DB_PATH=/data/easycad.db`, SPEC13 — magic-link accounts +
per-user settings). CAD working state is in-memory; nothing else touches disk.
The `trial_used` column and `anon_trial` table (SPEC14) are migrated in place on
first DB access when the new image boots — no manual step, additive and
rollback-safe.

## Routine deploy

Push to `main` (CI publishes `:latest`), then:

```sh
make deploy
```

## Isolation recap (why two containers)

- **app** — on `proxy-net` (reachable + internet egress for LLM) and `internal`
  (private link to the worker). Holds the LLM key and user data.
- **worker** — on `internal` only (`internal: true` → **no internet egress**),
  `read_only` rootfs, `cap_drop: ALL`, `no-new-privileges`, seccomp, mem/pids/cpu
  caps, non-root. Per-request: AST guard → fresh `setrlimit` child → tmpfs wiped.
- A worker breakout reaches no secrets, no network, no other users' data.
- Future hardening is one line — uncomment `runtime: runsc` (gVisor) on the
  worker service in `docker-compose-prod.yml`; no code change.

## Files

| File | Role |
|---|---|
| `.github/workflows/ci.yml` | test + build/push both images to ghcr.io |
| `Dockerfile` | app image (FastAPI + built `static/`, no CadQuery) |
| `worker/Dockerfile` | worker image (CadQuery/OCP + guard + rlimits) |
| `docker-compose-prod.yml` | server compose (proxy-net + redoproxy/monic labels) |
| `docker-compose.yml` | local "hosted-shape" compose for testing the split |
| `.env.prod.example` | production env template (copy → `.env.prod`) |
| `Makefile` | `run` (local), `build`, `install`, `deploy` |
