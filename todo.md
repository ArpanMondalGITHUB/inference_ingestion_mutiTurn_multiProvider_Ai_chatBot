# Project TODO — Multi-Provider LLM Chat + Observability

Status snapshot of the 7 required deliverables, split into **what you already have**
and **what is left to build**. Checkboxes track remaining work.

| # | Requirement | Status |
|---|---|---|
| 1 | Multi-provider support | ✅ Done |
| 2 | Streaming responses | ✅ Done |
| 3 | Docker Compose one-command setup | ✅ Done |
| 4 | Event-based architecture | 🟡 Partial |
| 5 | Latency + Throughput + Errors dashboards | ❌ Not started |
| 6 | PII redaction | ❌ Not started |
| 7 | Deploy on self-hosted k8s | ❌ Not started |

---

## ✅ What you already have

### 1. Multi-provider support — DONE
- `server/src/provider/` — `base.py` defines a `ChatProvider` protocol; concrete
  adapters for `anthropic_provider.py`, `open_ai_provider.py`, `gemini_provider.py`.
- `registry.py` holds one instance per provider + "which are configured" lookup.
- `GET /v1/api/providers` exposes configured providers and selectable models.
- Frontend lets the user pick provider/model (`chatui/src`).

### 2. Streaming responses — DONE
- `POST /v1/api/chat/stream` — SSE with `start` / `chunk` / `done` / `error` events.
- `services/ai.py` `stream_assistant` + provider `chat_stream`.
- Streaming telemetry records `chunkCount` and `timeToFirstChunkMs` (TTFB).

### 3. Docker Compose one-command setup — DONE
- `docker-compose.yml`: `backend` (FastAPI) + `frontend` (nginx-served SPA).
- `server/Dockerfile`, `chatui/Dockerfile`, `.dockerignore` for both.
- nginx reverse-proxies `/api/*` → backend on the internal network.
- SQLite persisted in the `backend_data` named volume.
- `docker compose up --build` → app on http://localhost:8080.

### 4. Event-based architecture — PARTIAL ✅ (foundation is there)
- `sdk/llm_event_tracker.py` — `LLMTracker` wraps every call, builds an
  `LLMInferenceEvent`, emits it fire-and-forget via `asyncio.create_task`.
- `POST /llm-events` ingestion: Bearer auth, size guard, batch/single, Pydantic
  validation, enrichment, idempotent `INSERT OR IGNORE` by `event_id`.
- `GET /llm-events[/{id}]` to query stored events.
- Events stored in the analytics-oriented `llm_inference_events` table (indexed).
- See what's still missing under "What's left" #4.

---

## ❌ / 🟡 What's left to do

### 4. Event-based architecture — harden it 🟡
The event *model* exists, but delivery is in-memory fire-and-forget (events lost on
crash, no backpressure). To honestly call it "event-based":
- [ ] Introduce a real broker/queue between emitter and store (Redis Stream / Kafka /
      RabbitMQ). Emitter publishes; a consumer drains into SQLite/Postgres.
- [ ] Add a consumer/worker process (separate service in compose).
- [ ] Add retry + dead-letter for failed events (currently errors are swallowed).
- [ ] (Optional) client-side batching before publish.
- **Decision needed:** which broker? Redis Streams is the lightest to add to compose.

### 5. Latency + Throughput + Errors dashboards ❌
Backend already stores `latency_ms`, `status`, `provider`, `model`, `started_at`,
token counts — so the data is there; you need aggregation endpoints + a UI.
- [ ] Backend: add metrics/aggregation endpoints over `llm_inference_events`, e.g.
      - `GET /v1/api/metrics/latency` — p50/p95/p99, avg, grouped by provider/model.
      - `GET /v1/api/metrics/throughput` — requests per interval (time buckets).
      - `GET /v1/api/metrics/errors` — error rate + error types over time.
- [ ] Frontend: a Dashboard page with charts (e.g. `recharts`) for the three panels.
- [ ] Add a nav link/route to the dashboard in the SPA.
- **Alternative:** ship Prometheus + Grafana instead of a custom UI (expose
  `/metrics` in Prometheus format). Pick one — custom UI is simpler to demo.

### 6. PII redaction ❌
Nothing redacts today; previews are only truncated (~300 chars), not scrubbed.
- [ ] Add a redaction utility (regex for emails, phone numbers, credit cards,
      SSNs, API keys/tokens; optionally names via a library like `presidio` /
      `scrubadub`).
- [ ] Apply it in `LLMTracker` before `inputPreview` / `outputPreview` and
      `raw_event_json` are built, so PII never reaches storage.
- [ ] (Optional) make redaction toggleable via env (`PII_REDACTION_ENABLED`).
- [ ] Add a couple of unit tests proving emails/phones get masked.
- **Decision needed:** regex-only (fast, zero deps) vs. a library like Microsoft
      Presidio (better recall, heavier). Regex-first is enough for the assessment.

### 7. Deploy on self-hosted k8s ❌
No manifests exist yet. Target: a local self-hosted cluster (minikube / k3s / kind).
- [ ] Push images to a registry (or load locally into the cluster).
- [ ] Write manifests under `k8s/`:
      - [ ] `backend` Deployment + Service (+ ConfigMap for non-secrets).
      - [ ] `frontend` Deployment + Service.
      - [ ] Secret for provider API keys + `LOG_INGESTION_KEY`.
      - [ ] PersistentVolumeClaim for the SQLite DB (or switch to Postgres — see note).
      - [ ] Ingress (or NodePort) to expose the frontend.
      - [ ] (If broker added in #4) Deployment/StatefulSet for Redis/Kafka + consumer.
- [ ] Document `kubectl apply -f k8s/` + how to reach the app in the README.
- **Note:** SQLite + a single-replica Deployment with a PVC works for a demo, but
      ReadWriteOnce means you can't scale the backend past 1 pod. Moving to Postgres
      unblocks real replicas — worth deciding before writing manifests.

---

## Suggested order of attack
1. **PII redaction** (#6) — smallest, self-contained, touches only the tracker.
2. **Dashboards** (#5) — data already exists; add endpoints + charts.
3. **Event architecture hardening** (#4) — add broker + consumer.
4. **k8s deploy** (#7) — last, since it packages everything above (incl. the broker).

## Open decisions to make first
- Broker for #4: **Redis Streams** (lightest) vs Kafka.
- Dashboards for #5: **custom React page** vs Prometheus + Grafana.
- PII for #6: **regex-only** vs Presidio.
- Storage for #7: stay on **SQLite + PVC** vs migrate to Postgres (needed for >1 replica).
