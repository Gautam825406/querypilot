# QueryPilot — Trusted Text-to-SQL Analytics Agent

Ask a business question in plain English and get a validated, read-only SQL
result with a chart and a plain-English explanation. SQL is planned against a
governed semantic layer, checked with a real parser **before** it runs, and
self-corrected from real database errors when it fails. Never guessed, never hidden.

> **Sample data only.** The demo dataset is a deterministic, synthetic
> e-commerce dataset (2,000 customers, 100 products, 5,460 orders — 5,142
> completed — 600 subscriptions across 2024–2025, 24 months). Every screen
> labels it "Sample data". No real personal data. The seed is versioned
> (`seed_meta`): if the stored seed version differs from the code's, the
> database is reseeded on startup instead of silently serving stale data.

## Architecture

```mermaid
flowchart LR
    U[User question] --> API[FastAPI]
    API --> G[LangGraph planner<br/>classify → resolve → plan → draft]
    G --> V[sqlglot validator<br/>SELECT-only · schema · PII · LIMIT<br/>dialect: duckdb / postgres]
    V -->|valid| C[Cache<br/>hash SQL + layer version + scope<br/>Redis when REDIS_URL set, memory otherwise]
    C --> E[Executor<br/>DuckDB read-only, or Postgres<br/>READ ONLY txn + statement timeout]
    E -->|DB error| X[Corrector · max 3 attempts<br/>re-validates every retry]
    X --> V
    E --> R[Explainer + Vega-Lite chart]
    R --> A[Audit log · SQLite]
    V -->|blocked| B[Blocked: rule named,<br/>never executed]
    W[Celery worker + beat<br/>scheduled insights, when Redis set] --> E
```

Data flow: question → FastAPI → intent classification → semantic-layer lookup →
LangGraph plan → SQL draft → **sqlglot validation (the hard boundary between
generation and execution)** → cache check → read-only execution with limits →
self-correction loop on error (max 3) → explanation + chart → audit record.

The component that writes SQL never holds database credentials; the component
that executes SQL never accepts unvalidated text.

## Measured results (run 2026-11-11, golden set of 50 cases)

All numbers below come from `python backend/evals/run_eval.py` — a real run of
the real ask path. Nothing here is a PRD target.

| Metric | Measured |
| --- | --- |
| Execution accuracy (50 golden cases) | **100% (50/50)** |
| Execution accuracy, answered cases only (36) | 100% (36/36) |
| Valid-SQL rate (cases where SQL was drafted) | 100% (36/36) |
| Self-correction success (broken draft fixed from DB error) | yes, in 1 attempt |
| Safety suite (destructive / stacked / comment-hidden / PII / out-of-schema) | **11/11 blocked (100%)** |
| Ambiguous / unmodelled / unsafe handling | 14/14 correct (clarifying / refused / blocked) |
| LLM mode in this run | `deterministic-fallback` (no API key set) — cost $0.00, 0 tokens per query |
| Backend tests | **56 passing** (`python -m pytest backend/tests -q`) |

### Ablation (same 50 cases, one layer added at a time)

| Config | Execution accuracy | Valid-SQL rate | Avg latency |
| --- | --- | --- | --- |
| (a) Direct-LLM baseline | **not run — no LLM key** (no number is claimed without running it) | — | — |
| (b) + Semantic layer only | 100% (50/50) | 100% | 41.8 ms |
| (c) + Validator & correction | 100% (50/50) | 100% | 136.5 ms |
| (d) Full system + cache/examples | 100% (50/50) | 100% | 145.7 ms cold; 116.9 ms warm, cache hit rate 72% on the warm second pass (36/36 answered cases hit) |

Why (b) already scores 100%: the deterministic planner drafts from the same
semantic layer the checks are written against, so on the modelled golden set
the validator and cache add safety and latency, not accuracy. The difference
shows up off the golden set — an invalid draft (e.g. a bad date literal) is
fixed by the correction loop instead of failing, and unsafe SQL is blocked
before execution. The warm cache barely helps latency here because local
DuckDB execution is already ~10 ms; the cache pays off against a remote
Postgres target.

### Spider subset — not run

`python backend/evals/spider.py --limit 50` was attempted and produced an
honest "not run" report (`backend/evals/spider_report.json`): the first 50
validation examples were fetched from HuggingFace, but the Spider sqlite
databases are not directly downloadable — the taoyds/spider and
taoyds/test-suite-sql-eval GitHub repos do not host them (they are distributed
via Google Drive) — so execution accuracy cannot be measured without them.
No Spider score is claimed.

PRD targets (e.g. "improve over baseline") are **targets, not results**, and are
not claimed anywhere in this README.

## Demo questions (expected answers, seeded data)

| Question | Expected answer |
| --- | --- |
| What is total revenue? | $517,171.84 |
| What is total revenue by month? | 24 rows, Jan 2024 $16,694.74 → Dec 2025 $24,701.04 (line chart) |
| What is total revenue by region? | North $184,999.70 (top), then South $130,982.28, East $103,748.77, West $97,441.09 |
| What are the top products by revenue? | Pulse Smartwatch Max $17,928.85 (top of 5) |
| How many orders were there? | 5,142 completed orders (of 5,460 total) |
| What is the average order value? | $100.58 (AVG of completed order amounts) |
| How many active customers do we have? | 353 (COUNT DISTINCT customers with active subscriptions) |
| What is the monthly churn rate? | Monthly churn % by month (line chart) |
| What is the churn rate? | Clarifying question (needs a period) — not a guess |
| What is our profit margin? | Honest refusal — `profit` is not modelled; nearest metric is `total_revenue` |
| Delete all orders from the database. | Blocked by the validator (read-only rule) — never executed |

## Upload your own CSV (Data tab)

Upload a CSV from the **Data** tab (or `POST /api/data/upload`, multipart,
no token needed for the demo workspace) and ask questions over it in plain
English — the generated SQL goes through the same sqlglot validator,
read-only executor and correction loop as the demo metrics.

- **Limits:** one `.csv` file, max 5 MB, 100,000 rows, 50 columns. UTF-8
  (latin-1 fallback), header row required, column names sanitised to
  `[a-z0-9_]` (duplicates after sanitising are rejected), types inferred
  from a sample (integer / float / date / text), files with no data rows
  rejected.
- **Storage:** real DuckDB table `upl_<id>_<name>` in the demo database;
  metadata (filename, columns + types, row count, workspace) in the audit
  SQLite DB. `GET /api/data/datasets` lists uploads; `DELETE
  /api/data/datasets/{id}` drops the table + metadata. Uploaded tables are
  allow-listed in the validator **for their workspace only** and shown in
  `GET /api/semantic/models`.
- **Questions that work** (name the dataset, e.g. "sample sales"): "How many
  rows are in sample sales?" (COUNT), "What is the total amount in sample
  sales?" (SUM), "What is the average amount in sample sales?" (AVG), "Top 5
  amount in sample sales" (ORDER BY + LIMIT), "Total amount by region in
  sample sales" (GROUP BY), "Show rows in sample sales" (SELECT + LIMIT).
  Two numeric columns matching one question → a clarifying question with
  options; a question the columns can't answer → an honest refusal naming
  the available columns.
- **Try it now:** `frontend/public/sample-sales.csv` (200 rows of sales
  data, date/region/product/quantity/amount) — download it from the Data tab.
  Measured live: total amount $80,082.71; by region West $23,488.36, East
  $20,066.79, South $18,730.77, North $17,796.79; 200 rows.
- **DuckDB only:** uploads need the DuckDB demo database. With `DATABASE_URL`
  set (Postgres target) the endpoint refuses with a plain-English error.
- **Not supported (honest):** follow-up questions over an upload ("by region"
  on its own) — the dataset must be named again in the question; date
  grouping only by month; joins between an uploaded table and the demo
  tables; filtering an upload by a value ("amount over 100 in sample
  sales" is not parsed — it falls back to the refusal path).

## Run locally (one command)

```bash
docker compose up --build
# API: http://localhost:8000  ·  Frontend: http://localhost:5173
```

> Docker Compose was **not smoke-tested** while building this: Docker is not
> installed in the build environment, so `docker-compose.yml` was validated by
> YAML parse + manual review only (services: postgres, redis, backend, worker,
> beat, frontend). Run `docker compose config` and then `up --build` to
> smoke-test it before relying on it.

Or without Docker:

```bash
cd backend && python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
cd ../frontend && npm install && npm run dev   # VITE_API_URL defaults to http://localhost:8000
```

The backend boots with **zero optional env vars** (DuckDB demo + in-memory
cache + deterministic-fallback planner), so the live Render deploy — which
sets none of them — keeps working unchanged.

## PostgreSQL target

Set `DATABASE_URL` to a `postgres://` / `postgresql://` URL and the backend
switches dialect (validator uses the `postgres` sqlglot dialect; execution
runs inside a `READ ONLY` transaction with a statement timeout, so the
executor can never write even if validation had a bug). Unset it and DuckDB
remains the default.

```bash
# Seed the same deterministic demo dataset into Postgres (2,000 customers,
# 100 products, 5,460 orders, 600 subscriptions — identical rows to DuckDB):
DATABASE_URL=postgresql://qp:qp-dev-only@localhost:5432/querypilot \
  python -m app.seed --postgres
# or, without the flag, seed DuckDB:
python -m app.seed
```

On startup the backend also seeds Postgres automatically if the `orders`
table is missing (a failure to reach Postgres is logged, never fatal — the
service still boots). `/api/health` reports the active `dialect` and whether
a database URL is configured.

## Redis cache + Celery worker

Set `REDIS_URL` (e.g. `redis://localhost:6379/0`) and the result cache moves
to redis-py (key = `sha256(scope|sql|layer_version)`, TTL from `QP_CACHE_TTL`,
invalidated on layer-version change); `/api/health` reports `cache_backend:
redis` only when Redis actually answers a ping — otherwise it falls back to
the in-memory cache and says so. Without `REDIS_URL`, everything works with
the in-memory cache.

Scheduled insights run on cadence only when Redis is set:

```bash
# Worker (executes due insights + evals) and beat (scans for due insights
# every 60s) — same backend image:
celery -A app.worker worker --loglevel=info
celery -A app.worker beat --loglevel=info
# or: docker compose up worker beat
```

Each due insight is re-validated against the current schema/layer (on failure
it is paused as `paused_on_schema_change` and the owner is alerted), executed
read-only, stored, and checked against its threshold. A notification fires
**only on breach** (or on pause), via `INSIGHT_WEBHOOK_URL` and/or
`SLACK_WEBHOOK_URL`; every delivery attempt is logged in the insight's run
history. `POST /api/insights/{id}/run-now` runs one insight immediately and
works without Redis/Celery. Without `REDIS_URL`, insights are stored-only and
the API/health endpoint says so honestly — Celery is never pretended to run.

## Auth & roles (JWT)

```bash
POST /api/auth/register   # {email, name, password} -> JWT (role always business_user unless an admin creates it)
POST /api/auth/login      # {email, password} -> JWT
GET  /api/auth/me         # current user (needs a token)
```

Passwords are hashed with stdlib `hashlib.pbkdf2_hmac` (100k iterations);
tokens are PyJWT HS256, 24h by default (`QP_JWT_EXPIRY_HOURS`). Roles:
`admin` (admin limits, evals/run, any insight), `analyst`, `business_user`.
Users and workspaces live in the audit SQLite DB (tables migrated on startup);
an authenticated user can only query their own workspace.

`/api/ask` works **without a token** (anonymous = demo workspace), so the live
demo never breaks. Mutating/admin endpoints require a token with the right
role: insight create/patch/delete, `GET/PATCH /api/admin/limits`,
`POST /api/evals/run`. The frontend has a small login card in the navbar
(Sign in → form with **Use demo account** / **Use demo admin** buttons); it
stores the JWT in `localStorage` and sends `Authorization: Bearer` on calls,
and the app still works fully when logged out.

Demo logins (seeded on first boot, demo workspace):

| Login | Password | Role |
| --- | --- | --- |
| demo@querypilot.local | demo123 | business_user |
| admin@querypilot.local | admin123 | admin |

## Environment variables

See `.env.example` (names only, no secrets). Every variable is optional.

| Variable | Purpose | Default when unset |
| --- | --- | --- |
| `GROQ_API_KEY` / `GEMINI_API_KEY` / `OPENAI_API_KEY` | LLM keys (explanation polish + direct-LLM eval baseline) | deterministic-fallback, $0 |
| `DATABASE_URL` | Postgres target (`postgres://…`) | DuckDB demo file |
| `REDIS_URL` | Redis cache + Celery broker | in-memory cache, insights stored-only |
| `INSIGHT_WEBHOOK_URL` / `SLACK_WEBHOOK_URL` | Breach/pause alerts | no notifications |
| `QP_JWT_SECRET` | JWT signing secret | `dev-only-change-me` (change in production) |
| `QP_JWT_EXPIRY_HOURS` | Token lifetime | 24 |
| `QP_ROW_LIMIT` / `QP_TIMEOUT_SECONDS` / `QP_CACHE_TTL` | Limits | 100 / 10s / 300s |
| `VITE_API_URL` | Frontend → backend URL | http://localhost:8000 |

## Run the evaluation (one command)

```bash
python backend/evals/run_eval.py
```

## How it works

- **Semantic layer** (`backend/semantic/models.yaml`): dbt-style YAML — 4 models
  (customers, orders, products, subscriptions), 5 metrics (`total_revenue`,
  `total_orders`, `average_order_value`, `active_customers`,
  `monthly_churn_rate`), 4 dimensions, explicit allowed joins. PII columns
  `customers.email` and `customers.phone` are marked blocked.
- **Planner**: a real LangGraph `StateGraph`
  (classify → resolve → plan → draft → validate → execute → correct → explain)
  invoked on every `/api/ask` call. With no LLM key it uses a deterministic
  rule-based planner + template explainer; if `GROQ_API_KEY` is set, the
  explanation is polished by Groq (drafting stays validated either way).
  `/api/health` reports `llm_mode` honestly (`deterministic-fallback` with no key).
- **Validator** (`sqlglot`, a real parser): single SELECT or WITH…SELECT only;
  rejects INSERT/UPDATE/DELETE/DDL, stacked statements and comments hiding a
  second statement; every table/column must exist in the schema and be reachable
  via the semantic layer; PII blocked; LIMIT 100 injected; timeout enforced.
  Blocked queries never reach the executor. Dialect-aware: `duckdb` by
  default, `postgres` when `DATABASE_URL` is set.
- **Executor**: SQLAlchemy dialect abstraction. DuckDB: read-only connection.
  Postgres: the same validated SQL runs inside a `READ ONLY` transaction with
  a statement timeout. Row cap + timeout enforced in code. Self-correction:
  max 3 attempts, fed the exact DB error + failed SQL + schema excerpt,
  re-validated before re-execution, stops on a repeated identical error. Every
  attempt is logged in the run.
- **Explainer**: written from the executed result rows (definition, period,
  filters stated). Rates/averages are never summed across groups. Chart
  selection from result shape: time series → line, category comparison → bar,
  single value → stat. A Vega-Lite spec is stored with every run.
- **Audit & cache**: every run (question, plan, SQL, validation, attempts,
  rows, duration, tokens/cost — $0 in deterministic mode) is stored in SQLite
  (`backend/querypilot.db`, gitignored) and exposed via `/api/runs` and
  `/api/history`. Result cache keyed by `hash(scope|sql|layer_version)`, TTL
  300s — in-memory by default, Redis when `REDIS_URL` is set (with automatic
  fallback to memory if Redis is unreachable). Scheduled insights are executed
  by the Celery worker (see above); their runs, thresholds and notification
  deliveries are stored in the audit DB.
- **Auth**: JWT (PyJWT) with roles `admin` / `analyst` / `business_user`;
  passwords hashed with stdlib pbkdf2_hmac. `/api/ask` stays open for the demo
  workspace; mutating and admin endpoints require a token with the right role.

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/health` (also `/` and `/health`) | Status + honest `llm_mode`, `dialect`, `cache_backend`, `scheduler`, `auth` |
| `POST /api/auth/register` · `POST /api/auth/login` · `GET /api/auth/me` | JWT auth (register, login, current user) |
| `POST /api/ask` | Ask a question (no token needed for the demo workspace) |
| `POST /api/data/upload` · `GET /api/data/datasets` · `DELETE /api/data/datasets/{id}` | Upload a CSV, list uploads, delete one (DuckDB only; no token needed for demo workspace) |
| `POST /api/ask/{run_id}/follow-up` | Follow-up reusing prior run context |
| `GET /api/runs/{id}` · `GET /api/history` | Run detail / past runs |
| `GET /api/semantic/metrics` · `GET /api/semantic/models` | Semantic explorer |
| `GET/POST /api/insights` · `PATCH/DELETE /api/insights/{id}` | Scheduled insights (auth required) |
| `POST /api/insights/{id}/run-now` · `GET /api/insights/{id}/runs` | Run an insight now / its run history |
| `POST /api/feedback` | Thumbs up/down + analyst correction |
| `GET/PATCH /api/admin/limits` | Row limits, timeouts, cost caps (admin only) |
| `POST /api/evals/run` | Run the golden-set evaluation + ablation (admin only) |

## Honest limits / non-goals

- Read-only, always. No writes, no unmodelled tables, no hidden SQL.
- Deterministic planner covers the 5 modelled metrics and their dimensions;
  anything else is refused or clarified, not improvised.
- **Postgres target exists** (`DATABASE_URL`), but no live Postgres was
  available in the build environment — the executor's read-only transaction
  is covered by a mocked-engine test, not a live Postgres run. Seed it with
  `python -m app.seed --postgres` and verify before relying on it.
- **Redis/Celery exist** (`REDIS_URL`), but no live Redis was available in
  the build environment — cache fallback to memory is tested (unreachable
  Redis degrades cleanly), the worker's scan/execute/notify logic is tested
  through the synchronous `run-now` path, but the Celery beat loop itself
  was not run end-to-end here.
- **Docker Compose was not smoke-tested** (no Docker in the build
  environment) — YAML parse + review only.
- **Direct-LLM baseline not run** — no LLM key is set, so the ablation
  reports "not run — no LLM key" and no baseline number is claimed.
- **Spider subset not run** — the Spider sqlite databases are not directly
  downloadable in this environment (see `backend/evals/spider_report.json`);
  no Spider score is claimed.
- Auth is JWT with a dev-default secret (`dev-only-change-me`): set
  `QP_JWT_SECRET` to a long random value in production. The demo logins are
  public demo credentials, not real accounts.
- **CSV uploads are DuckDB-only** (see "Upload your own CSV" above) and
  live in the same DuckDB file as the demo data. DuckDB forbids a read-write
  connection while a read-only one to the same file is alive in the process,
  so the executor disposes its engine after every query and the upload path
  retries a held connection briefly; a genuinely concurrent ask + upload can
  still make one upload retry. Upload question patterns are limited to
  count / sum / average / top-N / group-by / show-rows (see above).
- Workspace scoping is enforced in the planner/validator for the demo
  workspace; multi-workspace data isolation beyond that is not a goal of
  this build.
