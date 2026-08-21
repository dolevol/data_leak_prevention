# Architecture — Simplified DLP Platform

This document describes the system at five levels: user flows (including the endpoint
summary), system design, data model, core module structures and loading strategy.

---

## 1. User flows

The two resources have genuinely different flows, so they are documented separately.
Data Types are self-contained; Data Sets always depend on Data Types that already exist.

<details>
<summary><strong>Expand - 12 User Flows Diagrams (Data Types, Data Sets, Scan, Backend endpoints)</strong></summary>

### 1.1 Data Types

**Create** — `POST /api/datatypes`

```mermaid
flowchart LR
    A["Client sends<br/>name, type, content, threshold"] --> B{"Schema valid?"}
    B -- "no" --> B1["422<br/>blank name, empty content,<br/>threshold &lt; 1, unknown type"]
    B -- "yes" --> C["INSERT into data_types"]
    C --> D{"Name unique?"}
    D -- "no" --> D1["409<br/>name already exists"]
    D -- "yes" --> E["201 Created<br/>body carries the generated UUID"]
```

**List** — `GET /api/datatypes?limit=&offset=`

```mermaid
flowchart LR
    A["Client sends<br/>limit, offset"] --> B{"1 ≤ limit ≤ 200<br/>offset ≥ 0?"}
    B -- "no" --> B1["422"]
    B -- "yes" --> C["SELECT ... ORDER BY name<br/>LIMIT / OFFSET"]
    C --> D["200 OK<br/>array, possibly empty"]
```

**Read** — `GET /api/datatypes/{id}`

```mermaid
flowchart LR
    A["Client sends id"] --> B{"Path parameter is a UUID?"}
    B -- "no" --> B1["422<br/>rejected before the handler runs"]
    B -- "yes" --> C["SELECT by primary key"]
    C --> D{"Row found?"}
    D -- "no" --> D1["404"]
    D -- "yes" --> E["200 OK"]
```

**Update** — `PUT /api/datatypes/{id}`

```mermaid
flowchart LR
    A["Client sends<br/>the complete new representation"] --> B{"Schema valid?"}
    B -- "no" --> B1["422"]
    B -- "yes" --> C["SELECT by primary key"]
    C --> D{"Row found?"}
    D -- "no" --> D1["404"]
    D -- "yes" --> E["Overwrite every field<br/>PUT replaces, never merges"]
    E --> F{"New name free?"}
    F -- "no" --> F1["409"]
    F -- "yes" --> G["200 OK<br/>same id, new content"]
```

**Delete** — `DELETE /api/datatypes/{id}`

```mermaid
flowchart LR
    A["Client sends id"] --> B["SELECT by primary key"]
    B --> C{"Row found?"}
    C -- "no" --> C1["404"]
    C -- "yes" --> D["DELETE"]
    D --> E{"Referenced by a Data Set?"}
    E -- "yes" --> E1["409<br/>ON DELETE RESTRICT refuses"]
    E -- "no" --> F["204 No Content"]
```

### 1.2 Data Sets

**Create** — `POST /api/datasets`

```mermaid
flowchart LR
    A["Client sends<br/>name, data_type_ids"] --> B{"Schema valid?"}
    B -- "no" --> B1["422<br/>blank name, empty list,<br/>malformed UUID"]
    B -- "yes" --> C["Drop duplicate ids"]
    C --> D["SELECT ... WHERE id IN (...)"]
    D --> E{"All ids exist?"}
    E -- "no" --> E1["404<br/>lists the unknown ids"]
    E -- "yes" --> F["INSERT data set<br/>+ association rows"]
    F --> G{"Name unique?"}
    G -- "no" --> G1["409"]
    G -- "yes" --> H["201 Created<br/>data_type_ids returned flat"]
```

**List** — `GET /api/datasets?limit=&offset=`

```mermaid
flowchart LR
    A["Client sends<br/>limit, offset"] --> B{"Bounds valid?"}
    B -- "no" --> B1["422"]
    B -- "yes" --> C["SELECT ... ORDER BY name"]
    C --> D["Same SELECT already carries<br/>the members (LEFT OUTER JOIN)"]
    D --> E["200 OK<br/>each item flattened by from_model"]
```

**Read** — `GET /api/datasets/{id}`

```mermaid
flowchart LR
    A["Client sends id"] --> B{"Path parameter is a UUID?"}
    B -- "no" --> B1["422"]
    B -- "yes" --> C["SELECT by primary key<br/>+ eager member load"]
    C --> D{"Row found?"}
    D -- "no" --> D1["404"]
    D -- "yes" --> E["200 OK<br/>id, name, data_type_ids"]
```

**Update** — `PUT /api/datasets/{id}`

```mermaid
flowchart LR
    A["Client sends<br/>new name and membership"] --> B{"Schema valid?"}
    B -- "no" --> B1["422"]
    B -- "yes" --> C["SELECT by primary key"]
    C --> D{"Row found?"}
    D -- "no" --> D1["404"]
    D -- "yes" --> E{"All new ids exist?"}
    E -- "no" --> E1["404"]
    E -- "yes" --> F["Replace the list<br/>ORM diffs it into INSERT/DELETE"]
    F --> G{"New name free?"}
    G -- "no" --> G1["409"]
    G -- "yes" --> H["200 OK"]
```

**Delete** — `DELETE /api/datasets/{id}`

```mermaid
flowchart LR
    A["Client sends id"] --> B["SELECT by primary key"]
    B --> C{"Row found?"}
    C -- "no" --> C1["404"]
    C -- "yes" --> D["DELETE data set<br/>association rows cascade"]
    D --> E["204 No Content<br/>the Data Types survive"]
```

### 1.3 Scan

**Scan** — `POST /api/scan`

```mermaid
flowchart LR
    A["Client sends<br/>text, data_set_id"] --> B{"Schema valid?"}
    B -- "no" --> B1["422<br/>empty text, over 1 MB,<br/>malformed UUID"]
    B -- "yes" --> C["Load Data Set<br/>+ its Data Types in one query"]
    C --> D{"Data Set exists?"}
    D -- "no" --> D1["404"]
    D -- "yes" --> E["For each Data Type:<br/>pick its engine, count occurrences"]
    E --> F{"count >= threshold?"}
    F -- "no" --> G["skip this Data Type"]
    F -- "yes" --> H["add to detected_objects"]
    G --> I{"combine mode"}
    H --> I
    I -- "OR (default)" --> J{"any detection?"}
    I -- "AND" --> K{"did every<br/>Data Type fire?"}
    K -- "no" --> L["discard all detections"]
    K -- "yes" --> J
    L --> J
    J -- "no" --> J1["200<br/>{status: not matched}"]
    J -- "yes" --> J2["200<br/>{status: match, detected_objects}"]
```

### 1.4 Backend endpoints

Two logical modules, each with full CRUD. The verb decides the operation, the path decides
the identity — no `/create` or `/delete` ever appears in a URL.

```mermaid
flowchart LR
    subgraph DT ["/api/datatypes"]
        DT1["POST /<br/>201"]
        DT2["GET /?limit&offset<br/>200"]
        DT3["GET /{id}<br/>200 | 404"]
        DT4["PUT /{id}<br/>200 | 404 | 409"]
        DT5["DELETE /{id}<br/>204 | 404 | 409"]
    end
    subgraph DS ["/api/datasets"]
        DS1["POST /<br/>201 | 404 | 409"]
        DS2["GET /?limit&offset<br/>200"]
        DS3["GET /{id}<br/>200 | 404"]
        DS4["PUT /{id}<br/>200 | 404 | 409"]
        DS5["DELETE /{id}<br/>204 | 404"]
    end
    subgraph SC ["/api/scan"]
        SC1["POST /<br/>200 | 404 | 422"]
    end
```

| Status | When it is returned | Who produces it |
|--------|--------------------|-----------------|
| 422 | Body or parameter violates the schema (threshold 0, empty keyword list, malformed UUID) | Pydantic, before the endpoint runs |
| 404 | Valid id with no row, or `data_type_ids` pointing at a missing Data Type | The router, or `MissingDataTypesError` |
| 409 | Duplicate name, or deleting a Data Type that is still in use | Database constraints, via `ConflictError` |
| 500 | A CHECK constraint fired — the schemas failed to catch bad input, i.e. a bug | Re-raised `IntegrityError` |

</details>

---

## 2. System design

The service is organized into four **layers**. Presentation, Business, Persistence and Support.

```mermaid
flowchart TD
    Client["HTTP client<br/>curl / Swagger UI"] --> Main

    subgraph App ["single uvicorn process"]
        direction LR

        subgraph Core [" "]
            direction TB

            subgraph L1 ["Presentation layer — HTTP boundary"]
                direction LR
                Main["app/main.py<br/>FastAPI app, lifespan,<br/>exception handlers"]
                Routers["app/api/datatypes.py<br/>app/api/datasets.py<br/>app/api/scan.py<br/>APIRouter per module"]
                Schemas["app/schemas.py<br/>request validation,<br/>response shaping"]
            end

            subgraph L2 ["Business layer — data access and inspection logic"]
                direction LR
                Crud["app/crud.py<br/>every database query,<br/>no HTTP knowledge"]
                Matchers["app/matchers.py<br/>counting functions,<br/>thresholds, OR across Data Types"]
            end

            subgraph L3 ["Persistence layer"]
                direction LR
                Models["app/models.py<br/>ORM tables and constraints"]
                Db["app/db.py<br/>engine, session factory,<br/>get_session dependency"]
            end
        end

        subgraph L4 ["Support layer — cross-cutting"]
            direction TB
            Deps["app/api/deps.py<br/>SessionDep, LimitDep, OffsetDep"]
            Const["app/constants.py<br/>shared limits"]
            Cfg["app/config.py<br/>Settings"]
        end

        Main --> Routers
        Routers --> Crud
        Routers --> Matchers
        Crud --> Models
        Models --> Db
    end

    Db --> PG[("PostgreSQL 16<br/>docker-compose")]

    %% Every solid request-path link is declared above, so the purple set below is
    %% the contiguous range 0-6. Dashed links go after it: appending one can never
    %% shift the styled indices again.
    Deps -.->|"injected into"| Routers
    Routers -.->|"validates with"| Schemas
    Matchers -.->|"typed against"| Models
    Const -.-> Schemas
    Const -.-> Models
    Const -.-> Deps
    Cfg -.-> Db
    Db -.->|"yields session"| Deps

    style Core fill:none,stroke:none
    linkStyle 0,1,2,3,4,5,6 stroke:#7c3aed,stroke-width:3px
```

The purple arrows are the paths a request actually walks. A CRUD request follows the spine
`Client → Main → Routers → Crud → Models → Db → PostgreSQL`; a scan request branches at the
routers into `Matchers` instead, because the Data Types it inspects were already loaded by
`crud`. Every dashed arrow is a dependency that is reached into sideways.

| Layer | Modules | Role |
|-------|---------|------|
| Presentation | `main.py`, the routers, `schemas.py` | Owns the HTTP contract: routing, status codes, request/response shape |
| Business | `crud.py`, `matchers.py` | Every query and domain rule (conflicts, missing references), plus the whole inspection logic (counting, thresholds, OR/AND) — no HTTP knowledge |
| Persistence | `models.py`, `db.py` | The ORM tables, constraints, engine and session factory |
| Support | `deps.py`, `constants.py`, `config.py` | Cross-cutting: dependency injection, shared limits, environment settings — reached into by the other three, never called in sequence |

### Life cycle of a single request

Traced end to end on `POST /api/datatypes`, the write path that exercises every stage. The
sequence below is what the code actually does:

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant F as FastAPI
    participant S as schemas.py
    participant R as routers
    participant U as crud.py
    participant O as ORM session
    participant P as pool / db.py
    participant G as PostgreSQL

    C->>F: POST /api/datatypes {json}
    F->>S: validate into DataTypeCreate
    S-->>F: schema object (or 422, endpoint never runs)
    F->>P: resolve get_session
    P-->>F: AsyncSession (no connection yet)
    F->>R: call endpoint(payload, session)
    R->>U: create_data_type(session, payload)
    U->>U: payload.model_dump() -> dict
    U->>O: DataType(**dict) — in memory, id still None
    U->>O: session.add() — pending, still no SQL
    U->>O: commit()
    O->>P: check out a connection (first real need)
    P->>G: BEGIN
    O->>G: INSERT ... VALUES ($1::UUID, ...)
    O->>G: COMMIT
    G-->>O: ok
    U-->>R: DataType with id populated
    R-->>F: ORM object
    F->>S: response_model -> DataTypeRead
    S-->>C: 201 Created {json}
    F->>P: close session, return connection
    P->>G: ROLLBACK (reset before reuse)
```

**Phase 1 — HTTP text becomes a validated Python object.**
FastAPI reads the body, parses the JSON into a `dict`, and hands it to Pydantic, which builds a
`DataTypeCreate`. This is where coercion happens (`"keywords"` becomes `InspectionType.KEYWORDS`)
and where every validator in `schemas.py` runs. Invalid input is rejected as 422 **before the
endpoint function is entered at all**.

**Phase 2 — dependencies are resolved.**
FastAPI walks the endpoint signature, sees `SessionDep`, runs `get_session` up to its `yield`, and
passes the resulting `AsyncSession` in as an argument.

**Phase 3 — Pydantic object becomes an ORM object.**
Two lines in `crud.create_data_type` do the whole translation:

```python
data_type = DataType(**payload.model_dump())
session.add(data_type)
```

**Phase 4 — first contact with the database.**
`commit()` is the moment everything deferred becomes real.


**Phase 5 — ORM object becomes the response.**
`crud` returns the live `DataType`, whose attributes are still loaded because the session factory
sets `expire_on_commit=False`.

FastAPI then re-validates that object against `response_model`. For Data Types this is automatic
via `from_attributes`; for Data Sets it is the explicit `DataSetRead.from_model`, because the
stored shape is not the wire shape. Either way `response_model` acts as a whitelist: an attribute
the schema does not declare cannot reach the client.

**Phase 6 — teardown.**
FastAPI resumes `get_session` past its `yield`, the `async with` block closes the session, and the
connection goes back to the pool. The pool issues a `ROLLBACK` on return regardless of outcome, so
no transaction state can leak into whichever request borrows that connection next.

**The read path in one line.** A `GET` skips phase 3 entirely and inverts phase 4: `crud` issues a
`SELECT` (which opens the transaction implicitly, exactly as an `INSERT` would), never calls
`commit`, and the session closes with the pool's `ROLLBACK`.

---

## 3. Data model

The relationship between Data Sets and Data Types is many-to-many, so it is implemented with an
association table rather than a single foreign key.

```mermaid
erDiagram
    DATA_SETS  ||--o{ DATASET_DATA_TYPES : "has one row per member"
    DATA_TYPES ||--o{ DATASET_DATA_TYPES : "has one row per membership"

    DATA_TYPES {
        uuid    id          PK "generated by the server"
        varchar name        UK "100 chars, unique index"
        varchar description    "500 chars, nullable"
        varchar type           "CHECK: keywords"
        text_ARRAY content     "CHECK: cardinality > 0"
        integer threshold      "CHECK: >= 1"
    }

    DATA_SETS {
        uuid    id   PK "generated by the server"
        varchar name UK "100 chars, unique index"
    }

    DATASET_DATA_TYPES {
        uuid dataset_id   PK "FK -> data_sets.id, ON DELETE CASCADE"
        uuid data_type_id PK "FK -> data_types.id, ON DELETE RESTRICT"
    }
```

Both relationship labels describe the **same** association table, read from its two ends - there
is no second relationship hiding here. A row in `DATASET_DATA_TYPES` says "this Data Set includes
this Data Type"; looking at it from the Data Set you call it a member, looking at it from the Data
Type you call it a membership.

The association table has a **composite primary key** over both columns. That serves two purposes
at once: it identifies the row, and it makes linking the same Data Type to the same Data Set twice
impossible at the database level.

The two delete policies express a business decision:

- Deleting a **Data Set** removes only its memberships (`CASCADE`), because links are worthless
  without the set that owns them.
- Deleting a **Data Type** that is in use is refused (`RESTRICT`) and surfaces as 409, because a
  security policy should not silently empty out when one component is removed.

---

## 4. Module structure

### 4.1 `app/models.py`

```mermaid
classDiagram
    class InspectionType {
        <<str, Enum>>
        KEYWORDS = "keywords"
    }
    class DataType {
        <<Base>>
        +UUID id
        +str name
        +str|None description
        +InspectionType type
        +list~str~ content
        +int threshold
        __table_args__: CHECK threshold, CHECK content
    }
    class DataSet {
        <<Base>>
        +UUID id
        +str name
        +list~DataType~ data_types
        relationship(secondary, lazy="joined")
    }
    class dataset_data_types {
        <<Table>>
        dataset_id FK CASCADE
        data_type_id FK RESTRICT
    }
    DataType --> InspectionType : type
    DataSet ..> dataset_data_types : secondary
    dataset_data_types ..> DataType : links
```

### 4.2 `app/schemas.py`

Each resource follows the same three-way split: `*Create` and `*Update` describe what may come
in, `*Read` describes what goes out. `*Base` holds the fields and validators shared by the write
schemas, so a rule is written once.

```mermaid
classDiagram
    class DataTypeBase {
        +str name
        +str|None description
        +InspectionType type
        +list~str~ content
        +int threshold
        _clean_name()
        _clean_description()
        _clean_keywords()
    }
    class DataTypeCreate
    class DataTypeUpdate
    class DataTypeRead {
        +UUID id
        from_attributes = True
    }
    DataTypeBase <|-- DataTypeCreate
    DataTypeBase <|-- DataTypeUpdate
    DataTypeBase <|-- DataTypeRead

    class DataSetBase {
        +str name
        _clean_name()
    }
    class DataSetCreate {
        +list~UUID~ data_type_ids  Field(min_length=1)
        _unique_ids()
    }
    class DataSetUpdate
    class DataSetRead {
        +UUID id
        +list~UUID~ data_type_ids  unconstrained
        from_model(DataSet)
    }
    DataSetBase <|-- DataSetCreate
    DataSetCreate <|-- DataSetUpdate
    DataSetBase <|-- DataSetRead

    class ScanRequest {
        +str text
        +UUID data_set_id
        +Combine combine = OR
    }
    class ScanResult {
        +ScanStatus status
        +list~DetectedObject~|None detected_objects
    }
    class DetectedObject {
        +UUID id
        +str name
        +int match_count
    }
    class ScanStatus {
        <<str, Enum>>
        MATCH = "match"
        NOT_MATCHED = "not matched"
    }
    class Combine {
        <<str, Enum>>
        OR = "or"
        AND = "and"
    }
    ScanRequest --> Combine : selects
    ScanResult --> ScanStatus : reports
    ScanResult --> DetectedObject : lists
```

Both resources now share the same `*Base` shape, but they draw the line in different places, and
the reason is worth stating precisely: **a constraint may be inherited by the read schema only
when it is also an invariant of the stored row.**

`DataTypeBase` holds every field, `content` and its `min_length=1` included, and `DataTypeRead`
inherits all of it. That is safe because the database enforces the same rule -
`CHECK (cardinality(content) > 0)` - so a Data Type with no keywords cannot exist to be read back.

`DataSetBase` holds only `name`. `data_type_ids` is declared twice on purpose: `DataSetCreate`
constrains it with `Field(min_length=1)` because a Data Set with no members is a pointless
*request*, while `DataSetRead` leaves it unconstrained because nothing in the database prevents
such a *row*. Inheriting the request's constraint would turn a plain `GET` of a member-less Data
Set into a 500.

`DataSetRead` is built by `from_model` rather than `from_attributes`, because the ORM
exposes `data_types` (objects) while the contract requires `data_type_ids` (a flat array).


### 4.3 `app/matchers.py`

The whole inspection logic is one module of plain functions. It separates **mechanics** (how many
times did a rule fire) from **policy** (is that enough, and how do the rules combine) - with a
function per logic and a dispatch dict, and no classes at all.

```mermaid
classDiagram
    class counting_functions {
        <<module level>>
        _count_keywords(patterns, text) int
        _count_substrings(...) : future
        _count_regex(...) : future
        _count_llm(...) : future
    }
    class _COUNTERS {
        <<dict>>
        InspectionType.KEYWORDS : _count_keywords
    }
    class policy_functions {
        <<module level>>
        detect(data_types, text, combine) list~DetectedObject~
        _detect_one(data_type, text) DetectedObject|None
        _count(data_type, text) int
    }
    class Combine {
        <<str, Enum>>
        OR = "or"
    }
    policy_functions --> _COUNTERS : looks up by data_type.type
    _COUNTERS --> counting_functions : holds
    policy_functions --> Combine : takes as an argument
```

## 5 Eager loading strategy

`DataSet.data_types` is declared `lazy="joined"`. Some form of eager loading is not optional in
this service: touching a lazily-loaded collection would emit IO from an already-returned async
context and raise `MissingGreenlet`. The choice is therefore *which* eager strategy, and it was
made by measurement rather than by default.

Both endpoints now issue exactly **one** statement. 
1. Reading one Data Set:

```sql
SELECT data_sets.id, data_sets.name, data_types_1.id, data_types_1.name, ...
FROM data_sets
LEFT OUTER JOIN (dataset_data_types AS dataset_data_types_1
                 JOIN data_types AS data_types_1
                   ON data_types_1.id = dataset_data_types_1.data_type_id)
  ON data_sets.id = dataset_data_types_1.dataset_id
WHERE data_sets.id = $1::UUID
ORDER BY data_types_1.name
```

2. Listing a few Datasets:

```sql
SELECT anon_1.id, anon_1.name, data_types_1.id, ...
FROM (SELECT data_sets.id, data_sets.name
      FROM data_sets ORDER BY data_sets.name
      LIMIT $1 OFFSET $2) AS anon_1
LEFT OUTER JOIN (dataset_data_types AS dataset_data_types_1
                 JOIN data_types AS data_types_1
                   ON data_types_1.id = dataset_data_types_1.data_type_id)
  ON anon_1.id = dataset_data_types_1.dataset_id
ORDER BY anon_1.name, data_types_1.name
```

The alternative, `lazy="selectin"`, splits this into two statements: one for the parents, then one
for the members keyed on `WHERE parent_id IN (...)`. Its advantage is that parent columns travel
the wire once instead of once per member. 

```sql
-- 1
SELECT data_sets.id, data_sets.name
FROM data_sets ORDER BY data_sets.name
LIMIT $1 OFFSET $2                         -- params (0,2)     

-- 2
SELECT data_sets_1.id, data_types.* ...
FROM data_sets AS data_sets_1 JOIN ... JOIN ...
WHERE data_sets_1.id IN ($1, $2)                -- The 2 params from Query 1
ORDER BY data_types.name
```

Measured on this schema, that advantage does not pay for
the extra round trip:

| Scenario | `selectin` | `joined` |
|----------|-----------|----------|
| One Data Set, 5 members | 1.46 ms | **0.81 ms** |
| List of 30, 5 members each | 4.24 ms | **1.98 ms** |
| List of 50, 20 members each | 16.43 ms | **11.39 ms** |
| List of 200, 20 members each | 86.48 ms | **53.00 ms** |

The reason is the shape of these rows, not the row count. A `data_sets` row is `id` + `name`,
about 29 bytes, while a `data_types` row carries the keyword array. Over 200 Data Sets with 20
members each, duplicating the parent costs 116 KB against 1.99 MB of member data - **6%** - and
saves a full round trip. The trade-off reverses when the parent is the wide side: replaying the
same query with a 2 KB parent payload, `joined` took 20.7 ms against 12.7 ms for `selectin`'s two
statements combined.
