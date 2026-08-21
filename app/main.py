"""ASGI entry point - builds the FastAPI application and wires everything together.

This is the file uvicorn loads (``uvicorn app.main:app``). It performs four jobs:

1. Creates the database schema on startup and disposes the engine on shutdown.
2. Creates the ``FastAPI`` instance.
3. Registers the routers of ``app/api`` - one per logical API module.
4. Registers exception handlers that translate domain errors from ``app.crud``
   into HTTP status codes, so no endpoint needs its own try/except.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import datasets, datatypes, scan
from app.crud import ConflictError, MissingDataTypesError
from app.db import Base, engine


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    """Manage startup and shutdown of the application.

    Everything before ``yield`` runs once at startup, everything after it runs
    once at shutdown. ``create_all`` is issued through ``run_sync`` because the
    DDL API of SQLAlchemy is synchronous and has to be driven by the async
    connection. It is a deliberate shortcut for this assignment - a production
    deployment would run Alembic migrations instead.

    Args:
        _: The application instance, unused.

    Yields:
        None: Control returns to FastAPI while the service handles requests.
    """
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield
    # Closes every pooled connection so the process can exit cleanly.
    await engine.dispose()


app = FastAPI(
    title="Simplified DLP Platform",
    description="Manage inspection Data Types, group them into Data Sets, and scan text.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(datatypes.router)
app.include_router(datasets.router)
app.include_router(scan.router)


@app.exception_handler(ConflictError)
async def handle_conflict(_: Request, error: ConflictError) -> JSONResponse:
    """Return HTTP 409 when a database constraint rejected the change.

    Args:
        _: The incoming request, unused.
        error: The domain error raised by ``app.crud``.

    Returns:
        JSONResponse: A 409 response carrying the business-level reason.
    """
    return JSONResponse(status_code=409, content={"detail": str(error)})


@app.exception_handler(MissingDataTypesError)
async def handle_missing_data_types(_: Request, error: MissingDataTypesError) -> JSONResponse:
    """Return HTTP 404 when a Data Set references unknown Data Types.

    Args:
        _: The incoming request, unused.
        error: The domain error raised by ``app.crud``.

    Returns:
        JSONResponse: A 404 response listing the unknown identifiers.
    """
    return JSONResponse(status_code=404, content={"detail": str(error)})


@app.get("/health", tags=["Health"])
async def health() -> dict[str, str]:
    """Report that the process is alive.

    Returns:
        dict[str, str]: A constant payload used by container health checks.
    """
    return {"status": "ok"}
