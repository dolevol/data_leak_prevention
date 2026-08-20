# Simplified DLP Platform

A single backend service that detects sensitive content in text. It lets you define
**Data Types** (keyword-based inspection rules with a threshold), group them into
**Data Sets** (policies), and scan text against them.

The project is built with FastAPI on top of PostgreSQL, is fully asynchronous, and is delivered
in three iterations:

| Iteration | Scope | Status |
|-----------|-------|--------|
| 1 | Data Type and Data Set management, database, validation, tests, documentation | ✅ done |
| 2 | The `/api/scan` endpoint and the matching engine | ✅ done |
| 3 | Optimisation, concurrency, full Docker setup, functional extensions | ⏳ next |

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
| POST | `/api/scan` | Scan a text against a Data Set | 200 |

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

# Scan a text against the Data Set
curl -X POST http://127.0.0.1:8000/api/scan \
  -H 'Content-Type: application/json' \
  -d '{"text":"my ssn, my passport and my visa","data_set_id":"<DS_UUID>"}'
# -> {"status":"match","detected_objects":[{"id":"...","name":"...","match_count":3}]}

# Require every Data Type of the set to fire, instead of any one of them
curl -X POST http://127.0.0.1:8000/api/scan \
  -H 'Content-Type: application/json' \
  -d '{"text":"...","data_set_id":"<DS_UUID>","combine":"and"}'

# Deleting the Data Type while it is in use is refused with 409
curl -i -X DELETE http://127.0.0.1:8000/api/datatypes/<DT_UUID>
```

## Scanning semantics

`POST /api/scan` takes a text and a Data Set id and answers immediately — the caller never
receives a job id to poll.

- Every Data Type in the Data Set is evaluated **independently**; counts are never pooled.
- A Data Type matches when its **occurrence count reaches its threshold**. Repetitions count, so
  the same keyword found three times is a count of three (see `docs/QA.md` Q3).
- Keywords match **whole words only**: the keyword `pass` is not found in `password`, `bypass` or
  `my_pass`, but is found in `(pass)` and `Pass,`.
- Matching is **case-insensitive** — an engine that misses `PASSWORD` would be worthless.
- Data Types combine with **OR** by default: the scan matches when at least one of them did, and
  every Data Type that matched is listed in `detected_objects`.
- The combination is an open argument. Sending `"combine": "and"` requires *every* Data Type in
  the Data Set to reach its threshold; a partial hit is reported as not matched. The field is
  optional and defaults to `"or"`, so the behaviour required by the specification is the default.

```json
{"status": "match",
 "detected_objects": [{"id": "dt-uuid", "name": "Sensitive Keywords", "match_count": 3}]}
```

```json
{"status": "not matched"}
```

The response carries no `detected_objects` key at all when nothing matched.

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
- `text` — non-empty and at most 1,000,000 characters. The upper bound is real protection: an
  unbounded body is the denial-of-service vector for a scanning service.
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
├── matchers.py     # inspection logic: counting functions, thresholds, OR/AND
└── api/
    ├── deps.py         # shared dependencies (session, pagination bounds)
    ├── datatypes.py    # router for /api/datatypes
    ├── datasets.py     # router for /api/datasets
    └── scan.py         # router for /api/scan
tests/              # pytest suite running against a real database
docs/               # ARCHITECTURE.md and QA.md
```

## Further documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — user flows per operation, endpoints, system
  design, data model, module structure, and the extension points for further inspection types.
- [docs/QA.md](docs/QA.md) — aggregated questions, answers and design decisions (in Hebrew).
