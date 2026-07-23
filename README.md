# EasyCAD

**Build 3D-printable models by describing them in plain language.** You type one
change at a time ("add a 5 mm wall along the top edge", "round the vertical
corners 3 mm"); EasyCAD generates [CadQuery](https://cadquery.readthedocs.io)
code, runs it, and shows the 3D result. Models grow step by step — each prompt
adds one feature to the accumulated code. Export STL and print.

Three panels: a code editor (you can hand-edit), a 3D viewer, and a chat.

```
┌───────────────┬──────────────────┬──────────────┐
│  Code (Monaco) │   3D viewer      │   Chat        │
│  editable      │   (three.js)     │  describe a   │
│  CadQuery      │   orbit / STL    │  change →     │
│                │                  │  result       │
├───────────────┴──────────────────┴──────────────┤
│  Step timeline:  [0]─[1]─[2]─[3]  ← click to revert │
└──────────────────────────────────────────────────┘
```

**Well suited:** boxes, enclosures, organizers, brackets, mounts — prismatic
shapes built from cuts, fillets, chamfers, extrusions.
**Not suited:** organic/sculpted forms, threads/tight tolerances, large
multi-body assemblies.

---

## Quick start (Docker Compose)

The easiest way to run your own instance. You need Docker and an LLM API key
(DeepSeek by default — get one at <https://platform.deepseek.com>; OpenRouter
also works).

```sh
DEEP_SEEK_KEY=sk-your-key docker compose up --build
```

Then open **<http://localhost:8852>**.

This runs two containers: the **app** (web UI + API) and an isolated **worker**
that executes the generated CadQuery code with no network access and strict
resource limits. Your projects/settings persist in a `userdata` volume.

> To make each user supply their own key instead of a shared server key, set
> `EASYCAD_REQUIRE_USER_KEY=1` on the `app` service and users add their key after
> signing in.

---

## Quick start (local, no Docker)

For development or single-user desktop use. Needs Python 3.11+, [uv](https://docs.astral.sh/uv/),
and Node 18+.

```sh
# backend (CadQuery runs in-process — no worker needed locally)
uv venv .venv-poc
uv pip install -r requirements.txt        # includes cadquery (large, ~1 GB)
echo "DEEP_SEEK_KEY=sk-your-key" > .env

# frontend (builds into static/, served by the backend)
npm install
npm run build

make run        # → http://127.0.0.1:8852
```

For frontend hot-reload during development, run `npm run dev` (Vite proxies
`/api` to the backend) alongside `make run`.

> ⚠ Local mode executes the generated Python directly on your machine with no
> sandbox. It binds to `127.0.0.1` for a single trusted user. Do **not** expose
> it on a public address — use the Docker Compose setup (with the isolated
> worker) for anything multi-user.

---

## Using EasyCAD

1. **Describe one change** in the chat and press Send. EasyCAD may:
   - build it directly;
   - propose a **refined** wording for you to confirm/edit (toggle "refine" off
     to skip);
   - ask a **clarifying** question;
   - flag a request that **conflicts** with the current model.
2. **×3** generates three variations to preview and pick from.
3. **Edit the code** in the left panel and hit **Run** to apply manual changes.
4. **Timeline** (bottom): click any step to revert; continue from there.
5. **Export**: download the STL for the current model.
6. **Save / Load project**: a small text-only JSON of all steps (STL is
   regenerated on load) — your portable, git-friendly copy of the work.
7. **Sign in** (top right, by email magic link) to store your own LLM key and
   settings across sessions. Without signing in, your key lives only in the
   current browser session.

---

## Configuration (env)

| Variable | Purpose |
|---|---|
| `DEEP_SEEK_KEY` / `OPEN_ROUTER_KEY` | LLM provider key (server-side or dev fallback) |
| `EASYCAD_REQUIRE_USER_KEY` | `1` = every user must bring their own key (BYOK) |
| `EASYCAD_WORKER_URL` | Set → delegate execution to the worker (hosted); unset → local in-process |
| `EASYCAD_LOCAL_GUARD` | `1` = run the AST safety check in local mode too |
| `CADQUERY_WORKER_TIMEOUT_SECONDS` | Per-execution timeout (default 120) |
| `JWT_SECRET`, `APP_URL`, `MAIL_FROM`, `POST_SERVICE_URL`, `POST_USER`, `POST_PASS` | Email sign-in (magic link) — only needed for multi-user hosting |
| `EASYCAD_SESSION_TTL`, `EASYCAD_MAX_SESSIONS` | In-memory session eviction / cap |

---

## How it runs your code

- **Local mode:** CadQuery runs in a subprocess on your machine (crash-isolated,
  but not sandboxed — trusted single user).
- **Hosted mode:** the app delegates execution to a hardened worker container —
  no network egress, read-only filesystem, dropped capabilities, per-request
  CPU/memory/PID limits, and an AST allowlist. The LLM key never reaches the
  worker.

---

## More docs

- **[docs/DEPLOY.md](docs/DEPLOY.md)** — production deployment (ghcr images, CI,
  reverse proxy, `make install` / `make deploy`).
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — components, execution/isolation model,
  and main use cases.

## Tech stack

FastAPI · CadQuery 2.8 · Preact + Vite · Monaco · three.js · SQLite · Docker.
