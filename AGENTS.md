# AGENTS.md — working notes for AI agents

EasyCAD turns plain-language requests into **CadQuery** code → runs it → returns an
STL. Single-screen chat builder (code editor · 3D viewer · chat); each prompt adds
one feature to the accumulated code. Text-only (no image input). See `README.md`
for the product, `docs/ARCHITECTURE.md` for the system, `docs/spec*.md` for specs.

## Commands

The runtime venv is **`.venv-poc`** (CadQuery 2.8 + FastAPI/uvicorn/openai/pytest,
installed via `uv pip`). The Makefile's `PYTHON` points at it. Always run through it.

```bash
make run          # uvicorn app.main:app on 127.0.0.1:8852  (KILL port 8852 when done)
make build        # build frontend (Preact+Vite) → static/
make bench ARGS="run --set complete --backend reference"    # quality harness
make bench-test   # bench unit tests (bench/tests)

# App tests — there is NO `make test`. Run pytest directly with the CadQuery env:
CADQUERY_WORKER_TIMEOUT_SECONDS=120 XDG_CACHE_HOME=$PWD/.cache PYTHONDONTWRITEBYTECODE=1 \
  .venv-poc/bin/python -m pytest -q          # ~155 tests, ~2 min (CadQuery runs for real)

# bench tests / CLI need PYTHONPATH:
PYTHONPATH=bench/src:. XDG_CACHE_HOME=$PWD/.cache .venv-poc/bin/python -m pytest bench/tests -q
```

Env for the running server lives in **`.env`** (`DEEP_SEEK_KEY`, `OPEN_ROUTER_KEY`,
`JWT_SECRET`, …). `make run` loads it. Default LLM provider is **deepseek**, model
**`deepseek-v4-flash`**. `make release` gates on a clean tree + tests + bench
validation, then tags/pushes — don't run it casually.

## Layout

- `app/` — FastAPI backend. `main.py` (routes, sessions, trial gating), `llm.py`
  (providers + generator system prompt + in-turn repair), `refiner.py` (triage:
  `ready|refine|clarify|invalid`), `cadquery_exec.py` + `cq_worker.py` (execute in an
  isolated subprocess), `store.py`, `session_registry.py`, `db.py` (SQLite accounts),
  `skills.py`, `metrics.py`.
- `frontend/src/` — Preact + Vite, builds to `../static/`. Monaco editor, three.js viewer.
- `worker/` — the CadQuery execution worker (hosted mode; `EASYCAD_WORKER_URL`).
- `easycad_geom/` — shared geometry measurement package (mesh/BRep facts), used by bench.
- `bench/` — the quality harness (see below + `bench/README.md`, `docs/bench-SPEC.md`).
- `docs/` — `ARCHITECTURE.md`, `bench-SPEC.md`, and numbered `spec*.md` (spec11→ is the
  current line: SPEC11 chat builder, SPEC13 multi-tenant, SPEC14 trial, SPEC15 skills,
  SPEC16 generation-quality borrowings). `todo.md` is the backlog.

## Conventions & gotchas (hard-won)

- **Kill the dev server (port 8852) when a task is done** — don't leave it running.
- **`load_dotenv` ordering:** module-level `os.getenv(...)` config must be read AFTER
  `load_dotenv`. Values set only in `.env` are ignored otherwise (bit us in `app/main.py`
  and `bench/judge.py`). Read such config lazily, or after the dotenv load.
- **Coordinate contract:** generated parts sit on XY, `z_min ≈ 0`, centred on XY, Z-up.
  This is the FDM convention and what the bench grades — keep it.
- **CadQuery execution is isolated** in a subprocess (OCP can hard-crash); never `exec`
  generated code in-process. Local mode runs `cq_worker.py`; hosted mode hits the worker.
- **Repair loop:** on an exec failure the turn feeds the error (+ a targeted hint, see
  `llm.py:_repair_hint`) back to the model, up to `EASYCAD_MAX_REPAIR` extra attempts
  (default 2; 0 = one-shot). It fixes *crashes*, not wrong-but-executable geometry.
- Sessions are **in-memory**, cookie-keyed; only accounts/trial counters hit SQLite.
- The generator system prompt in `llm.py` is **already long and tuned** — adding blanket
  guidance to it has measured *net-negative* (SPEC16 §4.3). Prefer targeted, conditional
  guidance (e.g. repair hints fired only on the matching error).

## Bench (quality harness)

Measures what fraction of prompts produce a correct model. `complete` scenarios have a
CadQuery reference → **auto-graded** (bbox/volume/bodies/surface-deviation). `open`
scenarios (underspecified everyday objects) have no reference; the canonical grading is
**human blind-review** (`bench-SPEC §5.4`; §56 rejects *automatic* open grading) — an
automatic vision-judge (`bench judge`, `bench/judge.py`) exists but is **EXPERIMENTAL**
and reports a separate
`open_pass_rate@judge`, never the canonical metric. A live `product` run needs the server
up and spends real LLM money; `reference` backend is a free pipeline self-test.

## Working norms

- **Measure before shipping generation/prompt changes.** This project is measurement-
  driven: several plausible improvements (prompt blocks, clarification policy) measured
  neutral-or-negative and were reverted. Run the bench A/B; don't ship on faith.
- Don't commit or push unless asked; branch off `master` first if you do.
- After a nontrivial change: app tests (above) or `make bench-test` for bench work; keep
  `git diff --check` clean.
- Code-review output lands in `review.md` (git-ignored scratch, **fully overwritten each
  review run** — not appended). Fix findings, re-run tests.
