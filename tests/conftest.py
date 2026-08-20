"""Shared pytest fixtures.

Every test gets a freshly built schema in a dedicated test database and an HTTP
client that talks to the application in-process, without a running server.

The application's own ``get_session`` dependency is overridden so the endpoints
use the test engine instead of the one configured in ``app.config``.
"""

import os
import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base, get_session
from app.main import app

# The dlp_test database is created by scripts/init_test_db.sql when the
# postgres container initializes its volume for the first time.
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql+asyncpg://dlp:dlp@localhost:5432/dlp_test"
)


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Provide an HTTP client bound to a clean database.

    The schema is dropped and recreated per test, which keeps the tests fully
    independent at a negligible cost for three small tables.

    Yields:
        AsyncClient: A client whose requests are routed to the ASGI app directly.
    """
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_session() -> AsyncGenerator:
        """Yield a session from the test engine instead of the app engine."""
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    # ASGITransport skips the network stack entirely; the lifespan is not run,
    # which is why this fixture creates the schema itself.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client

    app.dependency_overrides.clear()
    await engine.dispose()


def data_type_payload(**overrides) -> dict:
    """Build a valid Data Type body, with optional field overrides.

    Args:
        **overrides: Fields to replace in the default payload.

    Returns:
        dict: A body accepted by ``POST /api/datatypes``.
    """
    payload = {
        "name": "Sensitive Keywords",
        "description": "Detects sensitive identifiers",
        "type": "keywords",
        "content": ["ssn", "passport", "visa"],
        "threshold": 2,
    }
    payload.update(overrides)
    return payload


async def create_data_type(client: AsyncClient, **overrides) -> dict:
    """Create a Data Type through the API and return the response body.

    Args:
        client: The test HTTP client.
        **overrides: Fields to replace in the default payload.

    Returns:
        dict: The created Data Type as returned by the API.
    """
    response = await client.post("/api/datatypes", json=data_type_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


UNKNOWN_ID = str(uuid.uuid4())
"""A syntactically valid UUID that is guaranteed not to exist in the database."""
