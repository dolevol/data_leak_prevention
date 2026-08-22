# Simplified DLP Platform

A single backend service that detects sensitive content in text. It lets you define
**Data Types** (keyword-based inspection rules with a threshold), group them into
**Data Sets** (policies), and scan text against them.

The project is built with FastAPI on top of PostgreSQL, is synchronous for the user (and fully-async in the background).

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
would use Alembic instead.

## Running the tests

The suite runs against a separate `dlp_test` database, created automatically when the container
initialises:

```bash
docker compose up -d
pytest -v
```

---

## Endpoints

Three logical modules, `/api/datatypes` and `/api/datasets` are supported with full CRUD. The HTTP verb decides the operation and the path
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

# Deleting the Data Type while it is in use is refused with 409
curl -i -X DELETE http://127.0.0.1:8000/api/datatypes/<DT_UUID>
```

## Scanning semantics

`POST /api/scan` takes a text and a Data Set id and answers immediately

- Every Data Type in the Data Set is evaluated **independently**;
- A Data Type matches when its **occurrence count reaches its threshold**. Repetitions count, so for example
  the same keyword found three times is a count of three.
- Keywords match **whole words only**: for example the keyword `pass` is not found in `password`, `bypass` or
  , but is found in `(pass)`, `Pass,` and `my_pass`.
- Matching is **case-insensitive**
- Data Types combine with **OR**: the scan matches when at least one of them did, and every Data
  Type that matched is listed in `detected_objects`.

```json
{"status": "match",
 "detected_objects": [{"id": "dt-uuid", "name": "Sensitive Keywords", "match_count": 3}]}
```

```json
{"status": "not matched"}
```

## Validation

The rules live in `app/schemas.py` and are returned as 422 before any endpoint code runs. The
numeric limits come from `app/constants.py`, which is also what `app/models.py` uses for its
column sizes and CHECK constraints — so the API layer and the database can never disagree.

- `name` — required, 1–100 characters after trimming, unique (a clash returns 409).
- `description` — optional, up to 500 characters; a blank value is stored as `null`.
- `type` — As for now, it must be `keywords` (represents a whole-keyword pattern); anything else is rejected by the enum.
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
├── matchers.py     # inspection logic: counting functions, thresholds, OR across Data Types
└── api/
    ├── deps.py         # shared dependencies (session, pagination bounds)
    ├── datatypes.py    # router for /api/datatypes
    ├── datasets.py     # router for /api/datasets
    └── scan.py         # router for /api/scan
tests/              # pytest suite running against a real database
docs/               # ARCHITECTURE.md
```

## Further documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — user flows per operation, endpoints, system
  design, data model, module structure, and the extension points for further inspection types.

## Non-Trivial decisions that I have made:

1. I've chosen the following delete object policy: if a user tries to delete a datatype that is used by an existing dataset - I block the action. In contrast, when a user tries to delete a dataset, it is approved. The first case is problematic because it's too powerful (might damage too many policies). I know that the best approach is to enable a soft-delete of datatypes (with a revival option), but I've found it as a lower priority at this moment.
2. I've decided to use a junction table to represent the many-to-many relationship between datasets and datatypes. Therefore, when a user tries to delete a dataset - all the dataset-datatype relations are being deleted (CASCADE).
3. For getting datasets with their datatypes from DB - I've decided to use "joined" query (instead of the suggested thumb-rule "selectin" approach) because it's more efficient in any case (get list of datasets / get a dataset by ID). Dataset scheme is thin (only id and name), so although "joined" creates a cartesian multiplication of rows  - it's pretty negligible comparing to 2 separated queries made by "selectin". I've compared the 2 approaches empirically with Claude (elaborated in ARCHITECTURE.md).  
4. The DB schema is created automatically at startup with "create_all". I know that a real production deployment would use Alembic instead, but it's an overkill for now.