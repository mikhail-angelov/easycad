.PHONY: run build install deploy release

PYTHON ?= .venv-poc/bin/python
LOCAL_ENV = CADQUERY_WORKER_TIMEOUT_SECONDS=120 XDG_CACHE_HOME=$(CURDIR)/.cache PYTHONDONTWRITEBYTECODE=1

# Deploy target host, read from the local .env (add a line: HOST=your.server)
HOST := $(shell grep '^HOST=' .env 2>/dev/null | cut -d '=' -f 2)

# ── local dev ────────────────────────────────────────────────────────────────
run:
	$(LOCAL_ENV) $(PYTHON) -m uvicorn app.main:app --host 127.0.0.1 --port 8852

build:
	@echo "Building frontend (→ static/)..."
	npm run build

# ── deployment (easycad.bconf.com) ───────────────────────────────────────────
# Images are built & pushed to ghcr.io by CI (.github/workflows/ci.yml).
# `install` seeds the server once; `deploy` pulls the latest images and restarts.
install:
	@echo "Provisioning server $(HOST)..."
	-ssh root@$(HOST) "mkdir -p /opt/easycad/data"
	scp ./.env.prod root@$(HOST):/opt/easycad/.env
	scp ./docker-compose-prod.yml root@$(HOST):/opt/easycad/docker-compose.yml

deploy:
	@echo "Deploying to $(HOST)..."
	ssh root@$(HOST) "docker pull ghcr.io/mikhail-angelov/easycad:latest"
	ssh root@$(HOST) "docker pull ghcr.io/mikhail-angelov/easycad-worker:latest"
	ssh root@$(HOST) "cd /opt/easycad && docker compose down"
	ssh root@$(HOST) "cd /opt/easycad && docker compose up -d"

# ── release ──────────────────────────────────────────────────────────────────
# Abort if the working tree is dirty, run tests + frontend build as a gate, then
# bump the patch of the latest vX.Y.Z tag and push the branch commits AND the new
# tag in a single push (CI builds the images on the tag). Requires a clean tree.
release:
	@[ -z "$$(git status --porcelain)" ] || { echo "✗ Uncommitted or untracked changes — commit/stash first:"; git status --short; exit 1; }
	@echo "Running tests…"
	$(LOCAL_ENV) $(PYTHON) -m pytest -q
	@echo "Building frontend (→ static/)…"
	npm run build
	@latest=$$(git tag --list 'v*' --sort=-v:refname | head -n1); \
	  latest=$${latest:-v0.0.0}; ver=$${latest#v}; \
	  major=$${ver%%.*}; rest=$${ver#*.}; minor=$${rest%%.*}; patch=$${rest##*.}; \
	  next="v$$major.$$minor.$$((patch + 1))"; \
	  branch=$$(git rev-parse --abbrev-ref HEAD); \
	  echo "Releasing $$latest → $$next on $$branch"; \
	  git tag -a "$$next" -m "Release $$next"; \
	  git push origin "$$branch" "$$next"; \
	  echo "✓ Pushed $$branch + $$next"
