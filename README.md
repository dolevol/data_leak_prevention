# Simplified DLP Platform

[![tests](https://github.com/dolevol/data_leak_prevention/actions/workflows/tests.yml/badge.svg)](https://github.com/dolevol/data_leak_prevention/actions/workflows/tests.yml)

A single backend service that detects sensitive content in text. It lets you define
**Data Types** (keyword-based inspection rules with a threshold), group them into
**Data Sets** (policies), and scan text against them.

The project is built with FastAPI on top of PostgreSQL, is synchronous for the user (and fully-async in the background for I/O ).

## Non-Trivial decisions that I have made:

* I've decided to focus on scalability - for future inspection engines (regex, prefixes, llm etc.) or for future logics (AND instead of OR).
* I've kept the modules decoupled by responsibility - `crud.py` has no HTTP knowledge and the scan core (`policy.py`, `engines.py`) has no DB knowledge - so the natural seam for splitting this into a policy-management service and a high-traffic scan microservice already exists, and neither module would need a rewrite. I'm deliberately not claiming it is "ready for microservices": an actual split would still have to solve the shared database, the `ON DELETE RESTRICT` that produces the 409, and the atomicity of creating a Data Set together with its members.
* I've interpreted "fully-sync" as for the user's experience only (waits for the response). The backend has to be async due to the DB I/O actions (to free the CPU for many future scan requests).
* I've chosen the following delete object policy: if a user tries to delete a datatype that is used by an existing dataset - I block the action. In contrast, when a user tries to delete a dataset, it is approved. The first case is problematic because it's too powerful (might damage too many policies). I know that the best approach is to enable a soft-delete of datatypes (with a revival option), but I've found it as a lower priority at this moment.
* I've decided to use a junction table to represent the many-to-many relationship between datasets and datatypes. Therefore, when a user tries to delete a dataset - all the dataset-datatype relations are being deleted (CASCADE).
* For getting datasets with their datatypes from DB - I've decided to use "joined" query (instead of the suggested thumb-rule "selectin" approach) because it's more efficient in any case (get list of datasets / get a dataset by ID). Dataset and Datatype scheme are thin (only few columns), so although "joined" creates a cartesian multiplication of rows  - it's pretty negligible comparing to 2 separated queries made by "selectin". I've compared the 2 approaches empirically with Claude (elaborated in ARCHITECTURE.md).  
* The DB schema is created automatically at startup with "create_all". I know that a real production deployment would use Alembic instead for versioning, but it's an overkill for now (the db scheme and data are being reset all the time).
* Docker files are heavy with in-line comments because the concept is new to me.
* I've decided not to build a caching mechanism for DataSets/DataTypes nor a hashing & caching mechanism for Scans due to the project's scope.
* I split the scan core into two files that each answer one question. `engines.py` holds the **mechanics** - how often does each pattern occur in the text - and never sees a Data Type. `policy.py` holds the **policy** - which Data Types fired - and never sees how counting works. An engine is stateless (patterns and text in, counts out), so one shared instance per type is safe across requests. A new inspection type is one `Engine` subclass plus one registry entry, and an AND mode would touch only `policy.py`. The full flow, with every function's input and output, is diagrammed in ARCHITECTURE.md, section 3.
* The matching algorithm (`engines.py`) is the result of the following decisions:
  * I count the **total occurrences** of the keywords and not the number of distinct keywords. It matches the "minimum occurrences" meaning of commercial DLP products, and the `match_count` in the response is exactly the number that was compared against the threshold.
  * I find the keywords with the **Aho-Corasick** algorithm (the `pyahocorasick` library): all the keywords of the Data Set are built into one tree of characters, and the text is read once while walking that tree. The scan time therefore depends on the length of the text and on the number of hits, and not on the number of keywords. On the largest accepted text (1,000,000 characters), 50 Data Types with 200 keywords each scan in about 20 ms, and 20 Data Types at the 1000-keyword maximum in about 35 ms (`pytest -m performance` prints the table).
  * The algorithm finds substrings, so I then check the characters on both sides of each hit to keep only whole words. I did not use a regex for either step: there is nothing to escape, a keyword that ends with a symbol (`c++`) is handled correctly, a keyword written by a user can never become a ReDoS pattern, and a keyword contained in another one (`york` inside `new york`) is still counted.
  * An underscore is **not** part of a word. For DLP, snake_case is a separator: `ssn` must be found inside `ssn_number`.
  * The text is lower-cased and read **once per scan** for all the Data Types together, so a keyword that several Data Types share is counted once and its count is reused.
  * The policy calls each engine **once per scan** with the patterns of all its Data Types together, then gives each Data Type the sum of its own patterns, so a keyword that several Data Types share is searched for once.
  * The tree is rebuilt on every scan and is not cached: building it for 400 keywords takes 0.09 ms, so a cache would add invalidation logic for no measurable gain.
  * I deliberately did **not** stop counting once the threshold is reached. It is much faster on a text full of hits, but `match_count` would then always equal the threshold and stop carrying information.
  * I also considered splitting the text into words and looking each keyword up in a dictionary. It needs no library, but it costs a fixed 40 ms per 1 MB even for a few keywords, and keywords such as `credit card` or `c++` need a second code path.
 

## Requirements

Docker and docker-compose. Python is needed only to develop or to run the tests on the host:
the images use 3.12, and the host suite was also verified on 3.14.

## Running the whole stack

One command builds the API image, starts PostgreSQL, waits for it to become healthy, and then
starts the service:

```bash
docker compose up -d --build
```

Interactive documentation (Swagger UI) is served at http://127.0.0.1:8000/docs, and the liveness
probe at http://127.0.0.1:8000/health.

## Demo from zero

**1. Install Docker** (once per machine):

- macOS / Windows: install Docker Desktop from https://docs.docker.com/get-docker/ and start it.
- Linux: `curl -fsSL https://get.docker.com | sh`, then `sudo usermod -aG docker $USER` and log in again.

```bash
docker --version          # the engine is installed
docker compose version    # the compose plugin is installed
```

**2. Get the code and run the demo:**

```bash
git clone https://github.com/dolevol/data_leak_prevention.git
cd data_leak_prevention
./scripts/demo.sh           # build, start, and walk the whole API with curl
./scripts/demo.sh --tests   # ...and finish with the full test suite in Docker
./scripts/demo.sh --fresh   # start from an empty database (deletes local data)
```

The script prints every command before running it, then the HTTP status and the response:
health check, two Data Types, a Data Set, a scan that matches (both rules, OR), a scan that does
not (whole words only), a 422 on an invalid threshold, the 409 that protects a Data Type in use,
and the clean-up. Every printed `curl` command can be copied and run on its own.

## Running the service locally instead

Useful while developing, because `--reload` restarts on every edit. Only the database runs in
Docker:

```bash
docker compose up -d db
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

The default `DATABASE_URL` already points at `localhost:5432`, so nothing needs configuring.
Inside Docker the compose file overrides it to `db:5432`, because `db` is the service name and
that is how one container reaches another.

Configuration is read from environment variables or a `.env` file (see `.env.example`):

| Variable | Default | Meaning |
|----------|---------|---------|
| `DATABASE_URL` | `postgresql+asyncpg://dlp:dlp@localhost:5432/dlp` | Connection string with an async driver |
| `DB_ECHO` | `false` | Log every SQL statement |

The schema is created automatically at startup with `create_all`. A real production deployment
would use Alembic instead.

## Running the tests

74 tests, selected with pytest markers:

| Marker | Files | Tests | Needs | What they prove |
|--------|-------|-------|-------|-----------------|
| `unit` | `test_engines.py`, `test_policy.py` | 16 + 8 | nothing | Engines: whole words, case, symbols, overlaps, one engine per inspection type. Policy: thresholds, total occurrences, OR, one pass per scan |
| `integration` | `test_datatypes.py`, `test_datasets.py`, `test_scan.py` | 20 + 11 + 13 | PostgreSQL | The HTTP contract end to end: status codes, validation, delete policies, scan verdicts |
| `performance` | `test_performance.py` | 5 unit + 1 integration | as marked | A 1,000,000-character text against Data Sets of up to 20,000 keywords: exact verdicts within time budgets, and a results table at the end of the run |

The integration suite runs against a separate `dlp_test` database, created automatically when
the database container initialises. Each test drops and recreates the schema, so tests never
depend on one another.

**Inside Docker - one command, same result on any machine.** Python 3.12 and every package
version come from the image, not from the host:

```bash
docker compose run --rm --build test
```

- `--build` rebuilds the test image first. Without it compose reuses the existing image, so tests
  edited since the last build would not be in it - a green run on old code.
- `--rm` deletes the container when the run ends, so finished test containers do not pile up.
- The `test` service sits in a compose profile, so a plain `docker compose up` never starts it.

**On the host - fastest while developing:**

```bash
pip install -r requirements-dev.txt
pytest -m unit           # under a second, no database, no Docker
docker compose up -d db  # the integration tests need PostgreSQL
pytest -v                # everything
pytest -m performance    # the scale scenarios, with a timing table at the end
```

**Sanity report - for showing someone the numbers are real.** The performance tests can also
write what they generated and what came back:

```bash
pytest -m performance --perf-report
```

- `tests/perf_report/input_text.txt` - the full 1,000,000-character text that was scanned.
- `tests/perf_report/report.md` - per scenario: the Data Set (input), every `match_count` returned
  (output), the expected count next to it, and the time. The end-to-end scenario also shows the
  raw API response.

The expected counts are computed independently of the engine, and the report prints a `grep`
command to recount any keyword in the text with a tool that has nothing to do with this service.
The folder is gitignored. To write it elsewhere, give a folder after the flag:
`--perf-report some/dir`.

The shipped image carries no test framework: the test tools live in a separate `test` stage of
the `Dockerfile` that a production build never executes (see
[ARCHITECTURE.md](docs/ARCHITECTURE.md#7-deployment)).

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
├── policy.py       # scan POLICY: per-Data-Type thresholds, OR across Data Types
├── engines.py      # scan MECHANICS: one stateless Engine per inspection type, counts occurrences
└── api/
    ├── deps.py         # shared dependencies (session, pagination bounds)
    ├── datatypes.py    # router for /api/datatypes
    ├── datasets.py     # router for /api/datasets
    └── scan.py         # router for /api/scan
tests/              # pytest suite running against a real database
docs/               # ARCHITECTURE.md
scripts/            # demo.sh (the interview walkthrough), init_test_db.sql (creates dlp_test)
```

## Further documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — user flows per operation, endpoints, system
  design, data model, module structure, and the extension points for further inspection types.

