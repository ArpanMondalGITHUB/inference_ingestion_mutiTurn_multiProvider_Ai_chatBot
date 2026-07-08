# Dockerizing the App with Docker + Nginx

A step-by-step tutorial for containerizing **this** project:

- **`chatui/`** — Vite + React (TypeScript) frontend, built with **pnpm**, served as static files by **Nginx**.
- **`server/`** — FastAPI + Uvicorn backend (Python 3.13), managed with **Poetry**.

We containerize each service, put **Nginx in front** as a reverse proxy (serving the built frontend and forwarding `/api` to the backend), and wire everything together with **Docker Compose**.

---

## Table of Contents

1. [Architecture overview](#1-architecture-overview)
2. [Prerequisites](#2-prerequisites)
3. [Frontend: multi-stage Dockerfile (build with pnpm, serve with Nginx)](#3-frontend-dockerfile)
4. [Nginx config (static + reverse proxy)](#4-nginx-config)
5. [Backend: FastAPI Dockerfile](#5-backend-dockerfile)
6. [`.dockerignore` files](#6-dockerignore-files)
7. [Tying it together with Docker Compose](#7-docker-compose)
8. [Environment variables](#8-environment-variables)
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

## 2. Prerequisites

- [Docker](https://docs.docker.com/get-docker/) 24+
- [Docker Compose](https://docs.docker.com/compose/) v2 (bundled with modern Docker Desktop)

Verify:

```bash
docker --version
docker compose version
```

---

## 3. Frontend Dockerfile

We use a **multi-stage build**: stage one compiles the Vite app with Node + pnpm, stage
two copies only the static `dist/` output into a tiny Nginx image.

Create **`chatui/Dockerfile`**:

```dockerfile
# ---------- Stage 1: build the Vite app ----------
FROM node:22-alpine AS build

# Enable pnpm via corepack (no global install needed)
RUN corepack enable

WORKDIR /app

# Install deps first (better layer caching). Copy only lockfiles.
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

# Copy the rest of the source and build
COPY . .

# VITE_* vars are baked in at build time — see the "Environment variables" section.
# Default to same-origin "/api" so Nginx can reverse-proxy it.
ARG VITE_API_URL=/api
ENV VITE_API_URL=$VITE_API_URL

RUN pnpm run build

# ---------- Stage 2: serve with Nginx ----------
FROM nginx:1.27-alpine AS runtime

# Replace the default server block with ours
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy the compiled assets
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
```

> **Why multi-stage?** The final image contains only Nginx + static files (~50 MB),
> not Node, `node_modules`, or your source. Smaller, faster, and less attack surface.

---

## 4. Nginx config

Your existing `chatui/nginx.conf` only serves static files. Replace it with a version
that **also reverse-proxies the API**, so the browser can hit `/api/...` on the same
origin and Nginx forwards it to the backend container.

Update **`chatui/nginx.conf`**:

```nginx
server {
    listen 8080;
    server_name _;

    root /usr/share/nginx/html;
    index index.html;

    # Gzip static assets
    gzip on;
    gzip_types text/plain text/css application/json application/javascript
               application/xml application/xml+rss text/javascript;

    # SPA fallback: any unknown path returns index.html so client-side routing works
    location / {
        try_files $uri $uri/ /index.html;
    }

    # Reverse proxy API calls to the FastAPI backend.
    # "backend" is the service name in docker-compose.yml; Docker DNS resolves it.
    location /api/ {
        # Strip the /api prefix before forwarding (FastAPI routes have no /api prefix)
        rewrite ^/api/(.*)$ /$1 break;

        proxy_pass http://backend:8000;
        proxy_http_version 1.1;

        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket support (your app uses the websockets package)
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection "upgrade";

        # Streaming responses (SSE / long-lived LLM streams): don't buffer
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }
}
```

> **Note on the `rewrite`:** your FastAPI routes are mounted at the root (e.g. `/health`,
> `/llm-events`). The `rewrite` strips `/api` so `/api/health` → `/health` on the backend.
> If you'd rather keep the prefix, add `root_path="/api"` to your `FastAPI()` and drop the
> `rewrite` line.

---

## 5. Backend Dockerfile

FastAPI + Uvicorn with Poetry. We install dependencies, then run Uvicorn pointing at
`server:app` with `--app-dir src` (your imports like `from core.config import ...` are
relative to `src/`).

Create **`server/Dockerfile`**:

```dockerfile
FROM python:3.13-slim AS base

# Keep Python lean and predictable in containers
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=2.1.1 \
    POETRY_VIRTUALENVS_CREATE=false

WORKDIR /app

# Install Poetry
RUN pip install "poetry==$POETRY_VERSION"

# Install dependencies first (cached unless the lock/pyproject changes)
COPY pyproject.toml poetry.lock ./
RUN poetry install --no-root --only main

# Copy application source
COPY src ./src

# The schema is loaded at startup by init_db(), which reads llm-events.sql from the
# server/ root (Path(__file__).parents[2]). It lives next to src/, so it must be copied
# too — otherwise the app crashes on startup with FileNotFoundError.
COPY llm-events.sql ./llm-events.sql

EXPOSE 8000

# App-dir points Uvicorn at src/ so intra-package imports resolve.
CMD ["uvicorn", "server:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000"]
```

> **`--host 0.0.0.0`** is required inside a container — the default `127.0.0.1` would
> only be reachable from *inside* the container, so Nginx couldn't reach it.

---

## 6. `.dockerignore` files

These keep build contexts small and stop secrets/artifacts from leaking into images.

Your `chatui/.dockerignore` already exists and is good. Make sure it contains:

```gitignore
node_modules/
dist/
.env
.env.*
.vite/
coverage/
npm-debug.log*
pnpm-debug.log*
pnpm-error.log*
```

Create **`server/.dockerignore`**:

```gitignore
.venv/
__pycache__/
*.pyc
*.db
.env
.env.*
.pytest_cache/
.mypy_cache/
```

> `*.db` excludes `src/db/mydb.db` so your local SQLite file isn't baked into the image.

---

## 7. Docker Compose

This orchestrates all three services. Create **`docker-compose.yml`** at the **repo root**
(next to `chatui/` and `server/`):

```yaml
services:
  backend:
    build:
      context: ./server
    environment:
      # Read by src/core/config.py via python-dotenv / os.getenv.
      # DATABASE_PATH points at the SQLite file inside the mounted volume below.
      - DATABASE_PATH=/app/src/db/mydb.db
      - CORS_ORIGINS=http://localhost:8080
      # Add your provider keys here (or use env_file below)
      # - GEMINI_API_KEY=...
      # - OPENAI_API_KEY=...
      # - ANTHROPIC_API_KEY=...
    env_file:
      - ./server/.env        # optional: keeps secrets out of this file
    volumes:
      # Persist the SQLite database across container restarts/rebuilds.
      - backend_data:/app/src/db
    restart: unless-stopped
    # No "ports:" — backend is only reachable via nginx on the internal network.
    # Uncomment to hit it directly during development:
    # ports:
    #   - "8000:8000"

  frontend:
    build:
      context: ./chatui
      args:
        # Baked into the JS bundle at build time. "/api" => same-origin via nginx proxy.
        VITE_API_URL: /api
    ports:
      - "8080:8080"
    depends_on:
      - backend
    restart: unless-stopped

volumes:
  backend_data:
```

> **No database container.** SQLite is a file, not a server — so there's nothing to run
> as its own service. The `backend_data` volume holds `mydb.db` so your data isn't lost
> when the container is recreated.
>
> **Service names are hostnames.** Inside the network, Nginx reaches the API at
> `backend:8000` — the name used in `nginx.conf`.

---

## 8. Environment variables

There are **two distinct kinds**, and mixing them up is the #1 source of confusion:

| | Frontend (`VITE_*`) | Backend |
|---|---|---|
| **When applied** | **Build time** (baked into the JS bundle) | **Runtime** (read on container start) |
| **How to set** | `build.args` in compose → `ARG`/`ENV` in Dockerfile | `environment` / `env_file` in compose |
| **Can change without rebuild?** | ❌ No — must rebuild the image | ✅ Yes — just restart the container |

**Frontend:** because Vite inlines `import.meta.env.VITE_API_URL` at build time, we set it
to `/api` so the app calls the same origin and Nginx proxies it. You no longer point the
browser at `http://127.0.0.1:8000` directly (that only works outside Docker).

**Backend:** your `src/core/config.py` reads vars like `CORS_ORIGINS`, `DATABASE_PATH`,
and provider API keys via `os.getenv`. Set them under `environment:` or in `server/.env`.
Make sure `CORS_ORIGINS` includes `http://localhost:8080` (the origin the browser uses),
and that `DATABASE_PATH` points inside the mounted volume (`/app/src/db/mydb.db`) so the
SQLite file persists.

> **Secrets:** never commit real API keys. Keep them in `server/.env` (git-ignored) and
> reference it with `env_file:`.

---

## 9. Build & run

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

## 10. Troubleshooting

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

**WebSocket / streaming responses cut off**
Confirm the `Upgrade`/`Connection` headers and `proxy_buffering off;` are present in the
`/api/` block (they are in the config above), and that `proxy_read_timeout` is generous.

**Changes to source aren't reflected**
Images are built once. After editing code, re-run `docker compose up --build`. For a live
dev loop, run the apps natively (`pnpm dev`, `uvicorn ... --reload`) and use Docker only
for staging/production-like runs.

---

## Summary of files to create / edit

| File | Action |
|------|--------|
| `chatui/Dockerfile` | **Fill in** (currently empty) — multi-stage build |
| `chatui/nginx.conf` | **Update** — add `/api` reverse proxy |
| `chatui/.dockerignore` | Already good ✅ |
| `server/Dockerfile` | **Create** |
| `server/.dockerignore` | **Create** |
| `docker-compose.yml` | **Create** at repo root |
| `server/.env` | **Create** (git-ignored) for secrets |

You're set — `docker compose up --build` and visit http://localhost:8080. 🚀