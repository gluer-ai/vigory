# Vigory.ai — Digital-Twin Graph Platform

A Miro-canvas-like digital twin: real-world entities (people, buildings,
rooms, vehicles, roads…) as typed graph nodes/edges in Neo4j, with an
ontology imported from `milinteltaxonomyontology.xlsx` (extendable at
runtime), a deterministic "scenario scoping" query (N-hop traversal from a
trigger entity), and an LLM agent layer (OpenAI + Anthropic via LiteLLM) for
extracting entities/relations from text and ranking relevance in a scoped
subgraph.

## Stack
- **Neo4j 5** (Docker) — graph store, Bolt driver
- **Backend**: FastAPI + Pydantic v2, async `neo4j` driver, `litellm`
  (provider-agnostic OpenAI/Anthropic client)
- **Frontend**: React + Vite, React Flow (canvas), Tailwind CSS v4, Radix UI,
  Lucide icons — see `frontend/DESIGN.md` for the design thesis

## Setup

```bash
git clone <repo> && cd vigory-ai
cp .env.example .env          # fill in NEO4J_PASSWORD, OPENAI_API_KEY or ANTHROPIC_API_KEY
docker compose up -d          # starts Neo4j on :7474 (browser) / :7687 (bolt)

npm run setup:all             # backend venv + pip install, frontend npm install
backend/venv/bin/python ontology/import_ontology.py   # one-time ontology seed

npm run dev                   # backend :8000, frontend :5173 (concurrently)
```

Backend requires Neo4j to be reachable; ingest/explain LLM endpoints require
`OPENAI_API_KEY` or `ANTHROPIC_API_KEY` in `.env` matching `LLM_PROVIDER`.

## Environment variables
See `.env.example`. Key ones:
- `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD`
- `LLM_PROVIDER` (`openai` | `anthropic`), `LLM_MODEL` (e.g. `gpt-4o`,
  `claude-sonnet-...`)
- `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`
- `VITE_API_BASE_URL` (frontend → backend)

## Architecture

```
ontology/import_ontology.py   one-time seed: xlsx -> ClassDef/LinkDef/VocabValue/
                               ConfidenceCode nodes in Neo4j (then extendable via API)
backend/app/
  db/neo4j_client.py          async driver singleton
  models/                     Pydantic: Entity, Link, ClassDef, LinkDef
  ontology/validate.py        domain/range + vocab + subclass checks (reads Neo4j, not enums)
  api/                        entities, links, schema, scenarios, ingest routers
  services/scoping.py         deterministic N-hop Cypher traversal (source of truth for scope)
  services/extraction_agent.py  text -> LLM JSON extraction -> validated "proposed" batch
  services/scenario_agent.py  ranks/annotates an already-scoped subgraph, never adds nodes
  llm/client.py               thin litellm.acompletion wrapper (provider via env)
frontend/src/
  components/graph/           EntityNode, RelationEdge, Canvas (React Flow), ScopeListView
  components/layout/          AppShell, LeftRail, Inspector, CanvasStates
  components/ui/              Button, Chip, Drawer, Select, Stepper (Radix + tokens)
  lib/api.ts, lib/types.ts    typed fetch client
```

### Ontology is graph data, not code
`ClassDef`/`LinkDef`/`VocabValue`/`ConfidenceCode` are Neo4j nodes seeded once
from the xlsx. New classes/link types can be added later via
`POST /schema/classes` / `POST /schema/links` — no redeploy.

### Scoping is deterministic; the LLM only annotates
`GET /scenarios/{id}/scope` is a pure Cypher variable-length-path query — the
LLM never decides what's in scope. `POST /scenarios/{id}/explain` layers a
relevance ranking on top of that already-bounded subgraph and can only
annotate or drop candidates, never add new ones.

### Extracted data is a hypothesis, not truth
`POST /ingest` runs the extraction agent and returns a `proposed` batch
(validated against the ontology, never auto-merged). `POST
/ingest/{batch_id}/commit` applies it explicitly.

## API surface
`POST/GET/PATCH /entities`, `POST/GET /links`, `GET/POST /schema/classes`,
`GET/POST /schema/links`, `GET /scenarios/{id}/scope`, `POST
/scenarios/{id}/explain`, `POST /ingest`, `POST /ingest/{batch_id}/commit`.
Full interactive docs at `http://localhost:8000/docs` once the backend is
running.

## Verification

```bash
docker compose up -d neo4j
backend/venv/bin/python ontology/import_ontology.py   # expect 484 classes, 106 links

cd backend && ./venv/bin/pytest -q                    # unit + integration tests
```

Manual: `curl` the endpoints above, or open the frontend, type a trigger
`entity_id`, and confirm the scoped subgraph renders on the canvas.

## Deploying

See [`DEPLOYMENT.md`](./DEPLOYMENT.md) for the full Railway deployment guide
(3 services: neo4j, backend, frontend), including the `.railway/railway.ts`
infrastructure-as-code definition, Dockerfiles, and the exact command
sequence to provision, seed, and go live.
