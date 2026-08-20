"""Integration tests for ``/api/scan``: the full flow from request to verdict."""

import uuid

from httpx import AsyncClient

from tests.conftest import UNKNOWN_ID, create_data_set, create_data_type


async def scan(client: AsyncClient, text: str, data_set_id: str):
    """Send a scan request and return the raw response.

    Args:
        client: The test HTTP client.
        text: The text to inspect.
        data_set_id: The Data Set to scan against.

    Returns:
        Response: The HTTP response, so tests can assert on status and body.
    """
    return await client.post("/api/scan", json={"text": text, "data_set_id": data_set_id})


async def test_match_returns_the_detected_data_type(client: AsyncClient) -> None:
    """Reaching the threshold produces a match carrying id, name and count."""
    data_type = await create_data_type(client, content=["ssn", "passport"], threshold=2)
    data_set = await create_data_set(client, data_type["id"])

    response = await scan(client, "my ssn and my passport", data_set["id"])
    assert response.status_code == 200
    assert response.json() == {
        "status": "match",
        "detected_objects": [
            {"id": data_type["id"], "name": data_type["name"], "match_count": 2}
        ],
    }


async def test_not_matched_omits_detected_objects(client: AsyncClient) -> None:
    """Below the threshold the body is exactly the payload the spec requires."""
    data_type = await create_data_type(client, content=["ssn", "passport"], threshold=2)
    data_set = await create_data_set(client, data_type["id"])

    response = await scan(client, "my ssn only", data_set["id"])
    assert response.status_code == 200
    # The key must be absent, not null.
    assert response.json() == {"status": "not matched"}


async def test_threshold_counts_repeated_occurrences(client: AsyncClient) -> None:
    """One keyword repeated three times reaches a threshold of three."""
    data_type = await create_data_type(client, content=["passport"], threshold=3)
    data_set = await create_data_set(client, data_type["id"])

    response = await scan(client, "passport, passport, passport", data_set["id"])
    assert response.json()["detected_objects"][0]["match_count"] == 3


async def test_whole_word_matching_through_the_api(client: AsyncClient) -> None:
    """A keyword embedded in a longer word does not trigger a match."""
    data_type = await create_data_type(client, content=["pass"], threshold=1)
    data_set = await create_data_set(client, data_type["id"])

    assert (await scan(client, "my password", data_set["id"])).json() == {"status": "not matched"}
    assert (await scan(client, "the pass", data_set["id"])).json()["status"] == "match"


async def test_or_logic_matches_when_a_single_data_type_fires(client: AsyncClient) -> None:
    """A Data Set matches when at least one of its Data Types matches."""
    identifiers = await create_data_type(
        client, name="Identifiers", content=["ssn", "passport"], threshold=2
    )
    salary = await create_data_type(client, name="Salary", content=["salary"], threshold=1)
    data_set = await create_data_set(client, identifiers["id"], salary["id"])

    body = (await scan(client, "my salary is high", data_set["id"])).json()
    assert body["status"] == "match"
    # Only the Data Type that actually fired is reported.
    assert [item["name"] for item in body["detected_objects"]] == ["Salary"]


async def test_all_matching_data_types_are_reported(client: AsyncClient) -> None:
    """Every Data Type that reached its threshold appears in the response."""
    identifiers = await create_data_type(
        client, name="Identifiers", content=["ssn", "passport"], threshold=2
    )
    salary = await create_data_type(client, name="Salary", content=["salary"], threshold=1)
    data_set = await create_data_set(client, identifiers["id"], salary["id"])

    body = (await scan(client, "ssn passport and salary", data_set["id"])).json()
    assert sorted(item["name"] for item in body["detected_objects"]) == ["Identifiers", "Salary"]


async def test_data_types_are_evaluated_independently(client: AsyncClient) -> None:
    """Counts are never pooled across Data Types to satisfy a threshold."""
    first = await create_data_type(client, name="First", content=["alpha"], threshold=2)
    second = await create_data_type(client, name="Second", content=["beta"], threshold=2)
    data_set = await create_data_set(client, first["id"], second["id"])

    # One hit each: two hits in total, but neither Data Type reaches its own threshold.
    assert (await scan(client, "alpha beta", data_set["id"])).json() == {"status": "not matched"}


async def test_matching_is_case_insensitive(client: AsyncClient) -> None:
    """Keywords match regardless of the casing used in the text."""
    data_type = await create_data_type(client, content=["password"], threshold=1)
    data_set = await create_data_set(client, data_type["id"])

    assert (await scan(client, "My PASSWORD is secret", data_set["id"])).json()["status"] == "match"


async def test_updating_a_data_type_changes_later_scans(client: AsyncClient) -> None:
    """The compiled-pattern cache never serves a stale rule after an update."""
    data_type = await create_data_type(client, content=["alpha"], threshold=1)
    data_set = await create_data_set(client, data_type["id"])
    assert (await scan(client, "beta only", data_set["id"])).json() == {"status": "not matched"}

    await client.put(
        f"/api/datatypes/{data_type['id']}",
        json={
            "name": data_type["name"],
            "description": None,
            "type": "keywords",
            "content": ["beta"],
            "threshold": 1,
        },
    )
    assert (await scan(client, "beta only", data_set["id"])).json()["status"] == "match"


async def test_unknown_data_set_returns_404(client: AsyncClient) -> None:
    """Scanning against a Data Set that does not exist yields 404."""
    response = await scan(client, "anything", UNKNOWN_ID)
    assert response.status_code == 404


async def test_invalid_requests_are_rejected(client: AsyncClient) -> None:
    """Empty text and a malformed Data Set id never reach the service."""
    assert (await scan(client, "", str(uuid.uuid4()))).status_code == 422
    assert (await scan(client, "text", "not-a-uuid")).status_code == 422


async def test_combine_defaults_to_or(client: AsyncClient) -> None:
    """Omitting ``combine`` keeps the behaviour the specification requires."""
    fires = await create_data_type(client, name="Salary", content=["salary"], threshold=1)
    silent = await create_data_type(client, name="Identifiers", content=["ssn"], threshold=1)
    data_set = await create_data_set(client, fires["id"], silent["id"])

    body = (await scan(client, "my salary", data_set["id"])).json()
    assert body["status"] == "match"
    assert [item["name"] for item in body["detected_objects"]] == ["Salary"]


async def test_combine_and_requires_every_data_type(client: AsyncClient) -> None:
    """With ``combine: and`` a partial hit is reported as not matched."""
    fires = await create_data_type(client, name="Salary", content=["salary"], threshold=1)
    silent = await create_data_type(client, name="Identifiers", content=["ssn"], threshold=1)
    data_set = await create_data_set(client, fires["id"], silent["id"])

    partial = await client.post(
        "/api/scan",
        json={"text": "my salary", "data_set_id": data_set["id"], "combine": "and"},
    )
    assert partial.json() == {"status": "not matched"}

    full = await client.post(
        "/api/scan",
        json={"text": "my salary and ssn", "data_set_id": data_set["id"], "combine": "and"},
    )
    body = full.json()
    assert body["status"] == "match"
    assert sorted(item["name"] for item in body["detected_objects"]) == ["Identifiers", "Salary"]


async def test_unknown_combine_value_is_rejected(client: AsyncClient) -> None:
    """Only the two documented combine modes are accepted."""
    data_type = await create_data_type(client, content=["ssn"], threshold=1)
    data_set = await create_data_set(client, data_type["id"])

    response = await client.post(
        "/api/scan",
        json={"text": "ssn", "data_set_id": data_set["id"], "combine": "xor"},
    )
    assert response.status_code == 422
