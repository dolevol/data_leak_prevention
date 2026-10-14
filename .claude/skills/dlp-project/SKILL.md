---
name: dlp-project
description: Context for the Simplified DLP Platform in this repository - what the service does, its module map and dependency rules, the design decisions already settled (with their reasoning), the invariants that must not break, and the environment gotchas. Load before reading, changing, reviewing or extending any code in this repo.
---

# Simplified DLP Platform - project context

Read this together with the `project-workflow` skill: that one describes *how* to work with this
user, this one describes *what* has already been built and decided here.

## What the service is

A Data Loss Prevention backend, built as a home assignment. Three logical API modules:

| Module | Resource | Purpose |
|--------|----------|---------|
| `/api/datatypes` | Data Type | An inspection rule: a list of keywords plus an occurrence threshold |
| `/api/datasets` | Data Set | A named policy grouping reusable Data Types (many-to-many) |
| `/api/scan` | - | Synchronous: takes text plus a Data Set id, returns `match` or `not matched` |

Matching semantics: each Data Type is evaluated independently, whole-word and case-insensitive,
counting **total occurrences** (repetitions included). A Data Type fires when its count reaches
**its own** threshold. Results are combined with **OR** across Data Types.

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2.0 async ORM · asyncpg · PostgreSQL 16 · Pydantic v2 ·
pytest · Docker Compose. Fully async end to end.

## Module map and the dependency rule

```
app/main.py        FastAPI app, lifespan (create_all), exception handlers -> HTTP codes
app/api/*.py       routers: HTTP only. Translate schema <-> crud/matchers, raise 404
app/api/deps.py    SessionDep, LimitDep, OffsetDep
app/schemas.py     Pydantic contract and every validation rule
app/crud.py        every database query. NO FastAPI import
app/matchers.py    the inspection logic. NO SQLAlchemy or session reference
app/models.py      ORM tables, constraints, the association table
app/db.py          engine, session factory, get_session dependency
app/config.py      Settings from environment
app/constants.py   shared limits, imported by schemas, models and deps
```

**The rule that must hold:** `crud.py` never learns about HTTP and `matchers.py` never learns
about the database. This is what the README's modularity claim rests on - verify it still holds
before claiming it.

## Decisions already settled - do not re-litigate without a reason

Each of these is documented in `docs/QA.md` with the full reasoning and the rejected options.

| Area | Decision | Why |
|------|----------|-----|
| Relationship | Association table `dataset_data_types` | The relation is many-to-many; a FK on Data Type would forbid reuse |
| Delete a Data Type in use | Blocked, `ON DELETE RESTRICT` -> HTTP 409 | A security policy must not silently empty out |
| Delete a Data Set | Allowed, `ON DELETE CASCADE` on the links | Links are worthless without their owner |
| Update semantics | `PUT` full replacement, not `PATCH` | Five fields; one validation schema instead of two |
| Threshold counting | Total occurrences, not distinct keywords | Matches commercial "minimum occurrences"; keeps `match_count` equal to the compared number |
| Whole-word matching | Aho-Corasick (`pyahocorasick`) finds substrings, then a boundary check; no regex | No metacharacters to escape, no `\b` failure on `c++`, no ReDoS from user keywords |
| Keyword search cost | One automaton per scan over the keywords of all Data Types, built in `prepare`, counts kept in the scan `memo`; not cached | Measured: 20x20 keywords on 1 MB went 217 ms -> 3.4 ms; build is 0.09 ms for 400 keywords |
| Early exit at the threshold | Rejected | `match_count` would always equal the threshold |
| Underscore | **Not** a word character | For DLP, snake_case is a separator: `ssn` must be found inside `ssn_number` |
| Eager loading | `lazy="joined"`, measured against `selectin` | The parent row is ~23 bytes and the children are wide, so duplication costs 6% and saves a round trip |
| Schema creation | `create_all` in the lifespan | Alembic is overkill at this scope; documented as the production upgrade |
| Combine mode | OR only; the `Combine` enum and the `detect` argument remain as the seam | AND was removed but must stay one enum member away |
| Matching engine | `BaseMatcher` ABC + one subclass per type + `_MATCHERS` registry | Template Method: the base owns the threshold comparison, subclasses own `count` |

## Invariants - breaking any of these is a bug

- `crud.py` imports no FastAPI; `matchers.py` references no session or SQLAlchemy.
- `list_data_sets` must call `result.unique()` before `scalars()`. A joined eager load of a
  collection returns one row per member, and SQLAlchemy raises rather than silently duplicating.
- `expire_on_commit=False` on the session factory. Without it, reading an attribute after commit
  triggers a refresh from an already-returned async context and raises `MissingGreenlet`.
- The relationship must stay eagerly loaded, for the same reason.
- `DataSetRead.data_type_ids` must stay **unconstrained**, while the write schemas keep
  `Field(min_length=1)`. Nothing in the database prevents a member-less Data Set, so a stored one
  must remain serializable. The general rule: an input constraint may be inherited by the read
  schema only when the database enforces the same invariant.
- `_commit` in `crud.py` maps SQLSTATE `23505` and `23503` to `ConflictError` and **re-raises
  everything else**. A CHECK violation is a bug and must surface as 500, never as a 4xx.
- A scan miss must serialize as exactly `{"status": "not matched"}` - this relies on
  `detected_objects` being nullable plus `response_model_exclude_none=True` on the route.

## Adding an inspection type - the extension path

Three edits, no caller changes:

1. Add a member to `InspectionType` in `app/models.py`.
2. Add a `BaseMatcher` subclass in `app/matchers.py` implementing `count(patterns, text, memo=None) -> int`.
3. Register one instance in `_MATCHERS`.

Overriding `prepare(patterns, text, memo)` is optional: it lets an engine see the patterns of all
its Data Types once per scan (this is how `KeywordMatcher` reads the text a single time).

An LLM engine would additionally need a config column (for example `config JSONB`) and would turn
`count` into `async def`, adding one `await` in `BaseMatcher.detect`.

## Running and verifying

```bash
docker compose up -d --build        # whole stack: api on :8000, db on :5432
docker compose up -d db             # database only, for local development
pip install -r requirements-dev.txt # runtime deps plus pytest and httpx
pytest -v                           # 66 tests: 20 datatypes, 11 datasets, 22 matchers, 13 scan
```

`docker compose up -d --build` is the acceptance check. Tests run on the host against the
`dlp_test` database, which lives inside the same PostgreSQL container as `dlp`.

## Environment gotchas seen repeatedly

- **The `dlp_postgres` container stops on its own** in this environment. Tests then fail with
  `OSError: Connect call failed ('127.0.0.1', 5432)`. The fix is always `docker compose up -d`,
  which is idempotent. Check this before debugging a "broken" test suite.
- **Demo data collides.** The dev `dlp` database accumulates rows from manual `curl` runs, and
  `name` is unique. Use randomised or prefixed names in throwaway experiments, and clean up after.
- **`docker exec` needs `-i`** to accept a heredoc on stdin. Without it the SQL silently does not
  run and the experiment quietly proves nothing.
- **`unzip -l` prints an `Archive:` header line**, so grepping its output for the archive name
  produces a false positive. Use `unzip -Z1` for entry names only.

## Out of scope, deliberately

Rate limiting, caching of Data Sets or scan results, Alembic migrations, secret management,
multi-stage Docker builds, and soft-delete of Data Types. Each is recorded in `docs/QA.md` with
the reason. Do not add them without being asked.
