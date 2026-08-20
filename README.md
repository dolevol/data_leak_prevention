# Simplified DLP Platform

A single backend service that detects sensitive content in text. It lets you define
**Data Types** (keyword-based inspection rules with a threshold), group them into
**Data Sets** (policies), and scan text against them.

The project is built with FastAPI on top of PostgreSQL, is fully asynchronous, and is delivered
in three iterations:

| Iteration | Scope | Status |
|-----------|-------|--------|
| 1 | Data Type and Data Set management, database, validation, tests, documentation | ✅ done |
| 2 | The `/api/scan` endpoint and the matching engine | ⏳ next |
| 3 | Optimisation, concurrency, full Docker setup, functional extensions | ⏳ pending |

---

## Requirements

Python 3.12 or newer, plus Docker and docker-compose to run the database.

## Installation and running

Start the database and install the dependencies:

```bash
docker compose up -d              # PostgreSQL 16 on port 5432
pip install -r requirements.txt
```

Then run the service:

```bash
uvicorn app.main:app --reload
```

Interactive documentation (Swagger UI) is served at http://127.0.0.1:8000/docs, and the liveness
probe at http://127.0.0.1:8000/health.

Configuration is read from environment variables or a `.env` file (see `.env.example`):

| Variable | Default | Meaning |
|----------|---------|---------|
| `DATABASE_URL` | `postgresql+asyncpg://dlp:dlp@localhost:5432/dlp` | Connection string with an async driver |
| `DB_ECHO` | `false` | Log every SQL statement |

The schema is created automatically at startup with `create_all`. A real production deployment
would use Alembic instead; the decision is recorded in `docs/QA.md`.

## Running the tests

The suite runs against a separate `dlp_test` database, created automatically when the container
initialises:

```bash
docker compose up -d
pytest -v
```

---

## Endpoints

Two logical modules, each with full CRUD. The HTTP verb decides the operation and the path
decides the identity.

| Verb | Path | Operation | Success |
|------|------|-----------|---------|
| POST | `/api/datatypes` | Create a Data Type | 201 |
| GET | `/api/datatypes?limit=&offset=` | Paginated list | 200 |
| GET | `/api/datatypes/{id}` | Read one | 200 |
| PUT | `/api/datatypes/{id}` | Full replacement | 200 |
| DELETE | `/api/datatypes/{id}` | Delete | 204 |
| POST | `/api/datasets` | Create a Data Set | 201 |
| GET | `/api/datasets?limit=&offset=` | Paginated list | 200 |
| GET | `/api/datasets/{id}` | Read one | 200 |
| PUT | `/api/datasets/{id}` | Full replacement | 200 |
| DELETE | `/api/datasets/{id}` | Delete | 204 |

End-to-end example:

```bash
# Create a Data Type
curl -X POST http://127.0.0.1:8000/api/datatypes \
  -H 'Content-Type: application/json' \
  -d '{"name":"Sensitive Keywords","description":"Detects personal identifiers",
       "type":"keywords","content":["ssn","passport","visa"],"threshold":2}'

# Create a Data Set referencing it
curl -X POST http://127.0.0.1:8000/api/datasets \
  -H 'Content-Type: application/json' \
  -d '{"name":"HR Policy","data_type_ids":["<DT_UUID>"]}'

# Deleting the Data Type while it is in use is refused with 409
curl -i -X DELETE http://127.0.0.1:8000/api/datatypes/<DT_UUID>
```

## Validation

The rules live in `app/schemas.py` and are returned as 422 before any endpoint code runs. The
numeric limits come from `app/constants.py`, which is also what `app/models.py` uses for its
column sizes and CHECK constraints — so the API layer and the database can never disagree.

- `name` — required, 1–100 characters after trimming, unique (a clash returns 409).
- `description` — optional, up to 500 characters; a blank value is stored as `null`.
- `type` — must be `keywords`; anything else is rejected by the enum.
- `content` — non-empty list, up to 1000 keywords; each is trimmed, may not be blank, is limited
  to 100 characters, and duplicates are dropped while the order is preserved.
- `threshold` — integer greater than or equal to 1 (a threshold of 0 would flag every text).
- `data_type_ids` — non-empty list of UUIDs; duplicates are dropped and an unknown id yields 404.
- `limit` / `offset` — `1 ≤ limit ≤ 200` and `offset ≥ 0`.

Validation is deliberately duplicated across two layers. Pydantic protects the API and produces
readable client errors; the database CHECK constraints protect the data itself. A CHECK violation
therefore means a bug in the service, and it surfaces as 500 rather than being disguised as a
client error.

---

## Project structure

```
app/
├── main.py         # entry point: app creation, lifespan, exception handlers
├── config.py       # configuration read from the environment
├── constants.py    # shared limits used by both the schemas and the ORM
├── db.py           # engine, session factory, per-request session dependency
├── models.py       # SQLAlchemy models: DataType, DataSet, association table
├── schemas.py      # Pydantic schemas and every validation rule
├── crud.py         # internal layer: all queries, no HTTP knowledge
└── api/
    ├── deps.py         # shared dependencies (session, pagination bounds)
    ├── datatypes.py    # router for /api/datatypes
    └── datasets.py     # router for /api/datasets
tests/              # pytest suite running against a real database
docs/               # ARCHITECTURE.md and QA.md
```

## Further documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — user flows per operation, endpoints, system
  design, data model, module structure, and the extension points for further inspection types.
- [docs/QA.md](docs/QA.md) — aggregated questions, answers and design decisions (in Hebrew).
