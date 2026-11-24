"""Shared pytest fixtures.

Every test gets a freshly built schema in a dedicated test database and an HTTP
client that talks to the application in-process, without a running server.

The application's own ``get_session`` dependency is overridden so the endpoints
use the test engine instead of the one configured in ``app.config``.
"""

import os
import uuid
from pathlib import Path
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


async def create_data_set(client: AsyncClient, *data_type_ids: str, name: str = "Policy") -> dict:
    """Create a Data Set through the API and return the response body.

    Lives here because three test modules need a Data Set as *setup*. Tests that
    exercise creation itself still POST inline, so the assertion under test is
    never hidden inside a helper.

    Args:
        client: The test HTTP client.
        *data_type_ids: Identifiers of the members.
        name: The Data Set name.

    Returns:
        dict: The created Data Set as returned by the API.
    """
    response = await client.post(
        "/api/datasets", json={"name": name, "data_type_ids": list(data_type_ids)}
    )
    assert response.status_code == 201, response.text
    return response.json()


UNKNOWN_ID = str(uuid.uuid4())
"""A syntactically valid UUID that is guaranteed not to exist in the database."""

PERFORMANCE_RESULTS: list[dict] = []
"""One entry per performance scenario, appended by ``test_performance.py``."""

PERFORMANCE_INPUT: dict = {}
"""The generated text and the planted keywords, registered by ``test_performance.py``."""


def pytest_terminal_summary(terminalreporter) -> None:
    """Print the performance table, and write the report files when asked to.

    A pytest hook: pytest calls it once, after every test has run.

    Args:
        terminalreporter: pytest's object for writing to the terminal.
    """
    if not PERFORMANCE_RESULTS:
        return
    terminalreporter.section("scan performance - 1,000,000-character text")
    for row in PERFORMANCE_RESULTS:
        terminalreporter.write_line(f"{row['scenario']:<44} {row['size']:<24} {row['elapsed']}")

    report_dir = terminalreporter.config.getoption("--perf-report")
    if report_dir:
        written = _write_performance_report(Path(report_dir))
        terminalreporter.write_line(f"\nsanity report written to: {written}")


def _write_performance_report(directory: Path) -> Path:
    """Write the scanned text and a readable input/output report for every scenario.

    Args:
        directory: Where to write; created if missing.

    Returns:
        Path: The report file.
    """
    directory.mkdir(parents=True, exist_ok=True)
    text_file = directory / "input_text.txt"
    text_file.write_text(PERFORMANCE_INPUT["text"])

    lines = ["# Scan performance - sanity report", "", "## Input", ""]
    lines.append(f"- Text: {len(PERFORMANCE_INPUT['text']):,} characters, saved in full as `{text_file.name}`.")
    lines.append("- Planted sensitive keywords (\"found\" is counted independently of the engine, by regex):")
    lines.append("")
    lines.append("| Keyword | Planted | Found in the final text |")
    lines.append("|---------|---------|-------------------------|")
    for keyword, planted in PERFORMANCE_INPUT["planted"].items():
        lines.append(f"| `{keyword}` | {planted} | {PERFORMANCE_INPUT['found'][keyword]} |")
    lines += ["", "Fewer are found than planted because the generated text is cut to the API's",
              "1,000,000-character limit; every expected count below uses the found number.",
              "", "Check any count yourself, with a tool that is not this service:", "", "```bash",
              f"grep -oP '(?<![a-z0-9])ssn(?![a-z0-9])' {text_file} | wc -l", "```", "",
              "Excerpt around the first `ssn`:", "", "```", PERFORMANCE_INPUT["excerpt"], "```", ""]

    for row in PERFORMANCE_RESULTS:
        lines += [f"## {row['scenario']}", "", "**Input - the Data Set:**", "", *row["data_set"], ""]
        lines += ["**Output:**", "", "| Data Type | match_count | Expected |", "|-----------|-------------|----------|"]
        for name, count in row["result"].items():
            lines.append(f"| {name} | {count} | {row['expected'].get(name, '-')} |")
        verdict = "OK - identical" if row["result"] == row["expected"] else "MISMATCH"
        lines += ["", f"- Verdict vs expected: **{verdict}**", f"- Time: {row['elapsed']}", ""]
        if row.get("response"):
            lines += ["Raw API response of the first scan (detected_objects shortened):", "", "```json",
                      row["response"], "```", ""]

    report = directory / "report.md"
    report.write_text("\n".join(lines))
    return report
