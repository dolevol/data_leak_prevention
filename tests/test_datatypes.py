"""Tests for the ``/api/datatypes`` module: CRUD behaviour and input validation."""

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import TEST_DATABASE_URL, UNKNOWN_ID, create_data_type, data_type_payload


async def test_create_returns_201_with_generated_id(client: AsyncClient) -> None:
    """Creating a Data Type stores it and returns a server-generated id."""
    body = await create_data_type(client)
    assert body["id"]
    assert body["name"] == "Sensitive Keywords"
    assert body["content"] == ["ssn", "passport", "visa"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"content": []},              # empty keyword list
        {"content": ["   "]},         # whitespace-only keyword
        {"threshold": 0},             # a threshold that would flag every text
        {"threshold": -1},            # negative threshold
        {"name": "   "},              # name that is empty after trimming
        {"type": "regex"},            # inspection type not supported yet
    ],
)
async def test_create_rejects_invalid_payloads(client: AsyncClient, overrides: dict) -> None:
    """Invalid bodies are rejected by Pydantic with HTTP 422."""
    response = await client.post("/api/datatypes", json=data_type_payload(**overrides))
    assert response.status_code == 422


async def test_create_trims_and_deduplicates_keywords(client: AsyncClient) -> None:
    """Keywords are trimmed and repeated entries are dropped, order preserved."""
    body = await create_data_type(client, content=[" ssn ", "ssn", "passport"])
    assert body["content"] == ["ssn", "passport"]


async def test_create_duplicate_name_returns_409(client: AsyncClient) -> None:
    """A second Data Type with the same name conflicts with the unique index."""
    await create_data_type(client)
    response = await client.post("/api/datatypes", json=data_type_payload())
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


async def test_get_returns_the_created_data_type(client: AsyncClient) -> None:
    """A Data Type can be fetched back by its id."""
    created = await create_data_type(client)
    response = await client.get(f"/api/datatypes/{created['id']}")
    assert response.status_code == 200
    assert response.json() == created


async def test_get_unknown_id_returns_404(client: AsyncClient) -> None:
    """A valid UUID that matches no row yields 404."""
    response = await client.get(f"/api/datatypes/{UNKNOWN_ID}")
    assert response.status_code == 404


async def test_get_malformed_id_returns_422(client: AsyncClient) -> None:
    """A path parameter that is not a UUID is rejected before reaching the handler."""
    response = await client.get("/api/datatypes/not-a-uuid")
    assert response.status_code == 422


async def test_list_is_ordered_and_paginated(client: AsyncClient) -> None:
    """Listing returns Data Types by name and honours limit and offset."""
    await create_data_type(client, name="B type")
    await create_data_type(client, name="A type")
    await create_data_type(client, name="C type")

    response = await client.get("/api/datatypes", params={"limit": 2, "offset": 1})
    assert response.status_code == 200
    assert [item["name"] for item in response.json()] == ["B type", "C type"]


async def test_list_rejects_invalid_pagination(client: AsyncClient) -> None:
    """Out-of-range pagination parameters are rejected with HTTP 422."""
    response = await client.get("/api/datatypes", params={"limit": 0})
    assert response.status_code == 422


async def test_update_replaces_every_field(client: AsyncClient) -> None:
    """PUT is a full replacement, so omitted-but-changed fields are overwritten."""
    created = await create_data_type(client)
    payload = data_type_payload(name="Renamed", content=["salary"], threshold=1)

    response = await client.put(f"/api/datatypes/{created['id']}", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == created["id"]
    assert body["name"] == "Renamed"
    assert body["content"] == ["salary"]
    assert body["threshold"] == 1


async def test_update_unknown_id_returns_404(client: AsyncClient) -> None:
    """Updating a Data Type that does not exist yields 404."""
    response = await client.put(f"/api/datatypes/{UNKNOWN_ID}", json=data_type_payload())
    assert response.status_code == 404


async def test_update_to_taken_name_returns_409(client: AsyncClient) -> None:
    """Renaming a Data Type onto an existing name conflicts."""
    await create_data_type(client, name="First")
    second = await create_data_type(client, name="Second")

    response = await client.put(
        f"/api/datatypes/{second['id']}", json=data_type_payload(name="First")
    )
    assert response.status_code == 409


async def test_delete_removes_the_data_type(client: AsyncClient) -> None:
    """Deleting an unreferenced Data Type returns 204 and makes it unreachable."""
    created = await create_data_type(client)

    response = await client.delete(f"/api/datatypes/{created['id']}")
    assert response.status_code == 204
    assert (await client.get(f"/api/datatypes/{created['id']}")).status_code == 404


async def test_delete_unknown_id_returns_404(client: AsyncClient) -> None:
    """Deleting a Data Type that does not exist yields 404."""
    response = await client.delete(f"/api/datatypes/{UNKNOWN_ID}")
    assert response.status_code == 404


async def test_database_rejects_invalid_threshold(client: AsyncClient) -> None:
    """The CHECK constraint catches a bad threshold that bypasses the API schemas.

    The API can never produce this row, so the write is issued as raw SQL. It
    proves the second line of defence exists: a bug in the validation layer, or
    a writer that does not go through this service, still cannot corrupt the data.
    """
    engine = create_async_engine(TEST_DATABASE_URL)
    statement = text(
        "INSERT INTO data_types (id, name, type, content, threshold)"
        " VALUES (gen_random_uuid(), 'Bad', 'keywords', ARRAY['ssn'], 0)"
    )
    with pytest.raises(IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(statement)
    await engine.dispose()
