# Deploying Vigory.ai to Railway

Three services, one Railway project:

```
┌───────────┐      public HTTPS       ┌──────────┐
│  frontend │ ───────────────────────▶│  backend │
│  (Vite    │                         │ (FastAPI)│
│  static)  │      VITE_API_BASE_URL  │          │
└───────────┘      baked at build     └────┬─────┘
                                            │ private network only
                                            ▼
                                       ┌──────────┐
                                       │  neo4j   │
                                       │ (bolt,   │
                                       │  no      │
                                       │  public  │
                                       │  domain) │
                                       └──────────┘
```

- **backend/** and **frontend/** each build from their own `Dockerfile` via
  `rootDirectory` — Railway auto-detects the Dockerfile, no Nixpacks config
  needed.
- **neo4j** runs the official `neo4j:5.26-community` image directly (no
  custom Dockerfile — nothing to build), with a persistent volume at
  `/data`, and is **never given a public domain** — the backend reaches it
  only over Railway's private network as `neo4j.railway.internal:7687`.
- Config lives in `.railway/railway.ts` (Railway's current
  [Infrastructure as Code](https://docs.railway.com/infrastructure-as-code)
  format — the older `railway.json`/`railway.toml` "Config as Code" is
  deprecated and closed to new services).

This document only prepares the repo. Nothing here deploys anything by
itself — you run the commands below when ready.

## Prerequisites

```bash
curl -fsSL railway.com/install.sh | bash   # Railway CLI
railway login
```

## 1. Link this repo to a Railway project

```bash
cd vigory   # repo root, where .railway/railway.ts lives
railway init   # or `railway link` if the project already exists
```

## 2. Preview the plan

```bash
railway config plan
```

Expect: `Plan: 4 to add` (the volume + 3 services: neo4j, backend, frontend).

## 3. Apply — creates the services (they will fail to boot fully until step 4)

```bash
railway config apply
```

## 4. Set secrets (never committed to git — `.railway/railway.ts` uses
   `preserve()` for all of these so `railway config apply` never overwrites
   them)

```bash
# Neo4j auth — pick a strong password, use the SAME one in both flags
railway variables --service neo4j \
  --set "NEO4J_PASSWORD=<strong-password>" \
  --set "NEO4J_AUTH=neo4j/<strong-password>"

# LLM key — whichever provider LLM_PROVIDER is set to (openai by default)
railway variables --service backend --set "OPENAI_API_KEY=<your-key>"
# or: railway variables --service backend --set "LLM_PROVIDER=anthropic" --set "ANTHROPIC_API_KEY=<your-key>"
```

Redeploy neo4j and backend after setting these (`railway redeploy --service neo4j`, same for backend).

## 5. Generate a public domain for the backend

```bash
railway domain --service backend
```

Copy the resulting URL (e.g. `https://backend-production-xxxx.up.railway.app`).

## 6. Point the frontend at the backend, then build

Vite bakes `VITE_API_BASE_URL` into the JS bundle at **build time** — it must
be set before the frontend's first successful build:

```bash
railway variables --service frontend --set "VITE_API_BASE_URL=https://backend-production-xxxx.up.railway.app"
railway redeploy --service frontend
railway domain --service frontend
```

## 7. Lock down CORS (optional but recommended)

By default the backend allows any origin (`CORS_ALLOWED_ORIGINS` unset →
`*`). Once you have the frontend's domain from step 6:

```bash
railway variables --service backend --set "CORS_ALLOWED_ORIGINS=https://frontend-production-xxxx.up.railway.app"
railway redeploy --service backend
```

## 8. Seed the ontology (one-time, idempotent — safe to re-run)

```bash
railway run --service backend python ontology/import_ontology.py
```

Expect: `Seeded ontology into bolt://neo4j.railway.internal:7687: {'classes': 485, 'links': 106, ...}`.

## Verify

```bash
curl https://backend-production-xxxx.up.railway.app/health
# {"status":"ok","neo4j":true}
```

Then open the frontend's domain in a browser and confirm the empty-state
canvas loads.

## Environment variables reference

| Service  | Variable                | Set how                          | Notes |
|----------|--------------------------|-----------------------------------|-------|
| neo4j    | `NEO4J_PASSWORD`         | manual (step 4)                   | never in git |
| neo4j    | `NEO4J_AUTH`             | manual (step 4)                   | `neo4j/<same password>` |
| neo4j    | `NEO4J_PLUGINS`          | `.railway/railway.ts` (`"[]"`)    | no APOC/GDS by default |
| backend  | `NEO4J_URI`              | `.railway/railway.ts`             | `bolt://neo4j.railway.internal:7687` |
| backend  | `NEO4J_USER`             | `.railway/railway.ts`             | `neo4j` |
| backend  | `NEO4J_PASSWORD`         | `.railway/railway.ts` (referenced from neo4j) | |
| backend  | `LLM_PROVIDER`           | `.railway/railway.ts`             | `openai` \| `anthropic` |
| backend  | `LLM_MODEL`              | `.railway/railway.ts`             | e.g. `gpt-4o` |
| backend  | `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | manual (step 4)     | never in git |
| backend  | `CORS_ALLOWED_ORIGINS`   | manual (step 7)                   | comma-separated; `*` if unset |
| frontend | `VITE_API_BASE_URL`      | manual (step 6)                   | baked in at **build** time |

## Updating the config later

Edit `.railway/railway.ts`, then:

```bash
railway config plan     # review the diff
railway config apply    # apply after confirming
```

`preserve()`-marked variables (all secrets above) are never touched by
`apply` — change those with `railway variables --set` directly.

## Notes / known trade-offs

- **Neo4j has no automated backup here.** `/data` is a persistent Railway
  volume, but if you need point-in-time recovery, add a scheduled backup
  (Railway cron service running `neo4j-admin database dump`, or switch to
  managed Neo4j AuraDB) before storing anything you can't afford to lose.
- **Single Neo4j instance, no replication.** Fine for this project's scale;
  revisit if write throughput or availability requirements grow.
- Backend and Neo4j test dependencies (`pytest`, `pytest-asyncio`, `httpx`)
  currently ship inside `backend/requirements.txt` and therefore inside the
  production image. They're small; split into a `requirements-dev.txt` later
  if image size becomes a concern.
