# Dockerizing the App with Docker + Nginx

A step-by-step tutorial for containerizing **this** project:

- **`chatui/`** — Vite + React (TypeScript) frontend, built with **pnpm**, served as static files by **Nginx**.
- **`server/`** — FastAPI + Uvicorn backend (Python 3.13), managed with **Poetry**.

We containerize each service, put **Nginx in front** as a reverse proxy (serving the built frontend and forwarding `/api` to the backend), and wire everything together with **Docker Compose**.

---

## Table of Contents

1. [Architecture overview](#1-architecture-overview)
9. [Build & run](#9-build--run)
10. [Common issues & troubleshooting](#10-troubleshooting)

---

## 1. Architecture overview

```
                    ┌──────────────────────────────────────┐
                    │            Docker network             │
                    │                                       │
  Browser  ───────► │  ┌─────────────┐      ┌────────────┐  │
  :8080             │  │   nginx     │─/api►│  backend   │  │
                    │  │ (frontend + │      │ (FastAPI / │  │
                    │  │  proxy)     │      │  uvicorn)  │  │
                    │  │  :8080      │      │  :8000     │  │
                    │  └─────────────┘      └─────┬──────┘  │
                    │                             │         │
                    │                     ┌───────▼───────┐ │
                    │                     │  SQLite file  │ │
                    │                     │ (mydb.db on a │ │
                    │                     │ mounted vol.) │ │
                    │                     └───────────────┘ │
                    └──────────────────────────────────────┘
```

**Key idea:** the browser only ever talks to Nginx (`:8080`). Nginx serves the compiled
React assets and forwards any request beginning with `/api` to the FastAPI container over
the internal Docker network. The backend is **not** exposed to the host unless you
explicitly publish its port.

> **Database:** this app uses **SQLite** — an in-process, file-based database. There is
> **no separate database container**; the backend reads/writes a single `mydb.db` file.
> We put that file on a Docker **volume** so your data survives container restarts.

---

## 2. Build & run

From the repo root:

```bash
# Build all images and start the stack
docker compose up --build

# Or run detached (in the background)
docker compose up --build -d
```

Then open **http://localhost:8080**.

Useful commands:

```bash
docker compose ps                 # list running services
docker compose logs -f backend    # tail backend logs
docker compose logs -f frontend   # tail nginx logs
docker compose down               # stop and remove containers
docker compose down -v            # also delete the backend_data volume (wipes the SQLite DB)
docker compose up --build backend # rebuild just one service
```

Quick health checks:

```bash
curl http://localhost:8080/api/health   # via nginx proxy -> backend
curl http://localhost:8080/             # frontend index.html
```

---

## 3. Troubleshooting

**`502 Bad Gateway` from Nginx**
The backend isn't reachable. Check `docker compose logs backend` — a crash on startup
(missing env var, DB connection) is common. Confirm Uvicorn binds `0.0.0.0`, and that the
`proxy_pass` host (`backend`) matches the compose service name.

**Frontend loads but API calls 404 / go to the wrong URL**
`VITE_API_URL` is baked at build time. If you changed it, you must **rebuild** the
frontend image: `docker compose up --build frontend`. Verify calls target `/api/...`.

**CORS errors in the browser console**
With the Nginx proxy, everything is same-origin so CORS shouldn't trigger. If it does,
you're probably still calling `http://127.0.0.1:8000` directly — check `VITE_API_URL`.
Otherwise add `http://localhost:8080` to `CORS_ORIGINS` for the backend.

**`pnpm install` fails with a lockfile error**
`--frozen-lockfile` requires `pnpm-lock.yaml` to match `package.json`. Run `pnpm install`
locally to refresh the lockfile, then rebuild.

**Poetry install is slow or fails**
Ensure both `pyproject.toml` and `poetry.lock` are copied before `poetry install`. If the
lock is stale, run `poetry lock` locally and rebuild.

**Changes to source aren't reflected**
Images are built once. After editing code, re-run `docker compose up --build`. For a live
dev loop, run the apps natively (`pnpm dev`, `uvicorn ... --reload`) and use Docker only
for staging/production-like runs.
