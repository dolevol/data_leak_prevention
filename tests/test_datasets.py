"""Tests for the ``/api/datasets`` module, including the referential rules
between Data Sets and Data Types.
"""

from httpx import AsyncClient

from tests.conftest import UNKNOWN_ID, create_data_type


async def test_create_links_existing_data_types(client: AsyncClient) -> None:
    """A Data Set stores the identifiers it was given and returns them flat."""
    first = await create_data_type(client, name="First")
    second = await create_data_type(client, name="Second")

    response = await client.post(
        "/api/datasets", json={"name": "HR Policy", "data_type_ids": [first["id"], second["id"]]}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "HR Policy"
    assert set(body["data_type_ids"]) == {first["id"], second["id"]}


async def test_create_with_unknown_data_type_returns_404(client: AsyncClient) -> None:
    """Referencing a Data Type that does not exist is reported as 404."""
    response = await client.post(
        "/api/datasets", json={"name": "HR Policy", "data_type_ids": [UNKNOWN_ID]}
    )
    assert response.status_code == 404
    assert UNKNOWN_ID in response.json()["detail"]


async def test_create_deduplicates_ids(client: AsyncClient) -> None:
    """The same Data Type listed twice is linked only once."""
    data_type = await create_data_type(client)

    response = await client.post(
        "/api/datasets",
        json={"name": "HR Policy", "data_type_ids": [data_type["id"], data_type["id"]]},
    )
    assert response.status_code == 201
    assert response.json()["data_type_ids"] == [data_type["id"]]


async def test_create_rejects_empty_membership(client: AsyncClient) -> None:
    """A Data Set with no Data Types would never match anything, so it is invalid."""
    response = await client.post("/api/datasets", json={"name": "Empty", "data_type_ids": []})
    assert response.status_code == 422


async def test_create_duplicate_name_returns_409(client: AsyncClient) -> None:
    """Data Set names are unique as well."""
    data_type = await create_data_type(client)
    payload = {"name": "HR Policy", "data_type_ids": [data_type["id"]]}
    await client.post("/api/datasets", json=payload)

    response = await client.post("/api/datasets", json=payload)
    assert response.status_code == 409


async def test_update_replaces_membership(client: AsyncClient) -> None:
    """PUT swaps the whole membership list rather than appending to it."""
    first = await create_data_type(client, name="First")
    second = await create_data_type(client, name="Second")
    created = (
        await client.post(
            "/api/datasets", json={"name": "HR Policy", "data_type_ids": [first["id"]]}
        )
    ).json()

    response = await client.put(
        f"/api/datasets/{created['id']}",
        json={"name": "HR Policy v2", "data_type_ids": [second["id"]]},
    )
    assert response.status_code == 200
    assert response.json()["data_type_ids"] == [second["id"]]


async def test_get_and_list_return_the_data_set(client: AsyncClient) -> None:
    """A Data Set is readable individually and through the listing."""
    data_type = await create_data_type(client)
    created = (
        await client.post(
            "/api/datasets", json={"name": "HR Policy", "data_type_ids": [data_type["id"]]}
        )
    ).json()

    assert (await client.get(f"/api/datasets/{created['id']}")).json() == created
    assert (await client.get("/api/datasets")).json() == [created]


async def test_get_unknown_id_returns_404(client: AsyncClient) -> None:
    """A valid UUID that matches no Data Set yields 404."""
    assert (await client.get(f"/api/datasets/{UNKNOWN_ID}")).status_code == 404


async def test_delete_data_type_in_use_returns_409(client: AsyncClient) -> None:
    """The ON DELETE RESTRICT constraint protects a Data Type that is in use."""
    data_type = await create_data_type(client)
    await client.post(
        "/api/datasets", json={"name": "HR Policy", "data_type_ids": [data_type["id"]]}
    )

    response = await client.delete(f"/api/datatypes/{data_type['id']}")
    assert response.status_code == 409
    assert "referenced" in response.json()["detail"]


async def test_delete_data_type_succeeds_after_data_set_removed(client: AsyncClient) -> None:
    """Once the referencing Data Set is gone, the Data Type can be deleted."""
    data_type = await create_data_type(client)
    data_set = (
        await client.post(
            "/api/datasets", json={"name": "HR Policy", "data_type_ids": [data_type["id"]]}
        )
    ).json()

    assert (await client.delete(f"/api/datasets/{data_set['id']}")).status_code == 204
    assert (await client.delete(f"/api/datatypes/{data_type['id']}")).status_code == 204


async def test_delete_data_set_keeps_its_data_types(client: AsyncClient) -> None:
    """Deleting a Data Set removes only the memberships, never the Data Types."""
    data_type = await create_data_type(client)
    data_set = (
        await client.post(
            "/api/datasets", json={"name": "HR Policy", "data_type_ids": [data_type["id"]]}
        )
    ).json()

    await client.delete(f"/api/datasets/{data_set['id']}")
    assert (await client.get(f"/api/datatypes/{data_type['id']}")).status_code == 200
