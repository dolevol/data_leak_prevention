"""Performance tests - the scan core under realistic and worst-case load.

Each scenario builds a Data Set of many Data Types, scans the largest text the
API accepts (1,000,000 characters), and asserts two things: the verdict is
exactly right, and the scan finishes inside a generous time budget. The budgets
are about 10x the measured times, so a slow CI machine passes while an accidental
change of complexity - one pass per Data Type, or per keyword - fails loudly.

A results table is printed at the end of the run (see ``pytest_terminal_summary``
in ``conftest.py``). Run only these tests with::

    pytest -m performance

For a sanity check to show someone, ``--perf-report DIR`` also writes the full
generated text and a per-scenario report of input, output and expected output::

    pytest -m performance --perf-report        # writes to tests/perf_report/
"""

import json
import random
import re
import string
import time
import uuid

import pytest
from httpx import AsyncClient

from app.constants import MAX_SCAN_TEXT_LENGTH
from app.models import DataType, InspectionType
from app.policy import evaluate
from tests.conftest import PERFORMANCE_INPUT, PERFORMANCE_RESULTS, create_data_set, create_data_type

pytestmark = pytest.mark.performance

# Keywords that really occur in the text, and how many times each is planted.
PLANTED = {"ssn": 400, "passport": 300, "credit card": 200, "iban": 100}


def build_text(seed: int = 7) -> str:
    """Build a 1,000,000-character text of random words with known sensitive keywords.

    Args:
        seed: Seed for the random generator, so every run scans the same text.

    Returns:
        str: Exactly ``MAX_SCAN_TEXT_LENGTH`` characters.
    """
    rng = random.Random(seed)
    vocabulary = ["".join(rng.choices(string.ascii_lowercase, k=rng.randint(3, 10))) for _ in range(20_000)]
    planted = [keyword for keyword, times in PLANTED.items() for _ in range(times)]
    words = rng.choices(vocabulary, k=150_000)
    for keyword in planted:
        words.insert(rng.randrange(len(words)), keyword)
    text = " ".join(words)
    # Pad or cut to the exact limit. The cut may drop a few planted keywords, which is
    # why the expected counts are recomputed from the final text (``expected_count``).
    return (text + " " + "x" * MAX_SCAN_TEXT_LENGTH)[:MAX_SCAN_TEXT_LENGTH]


TEXT = build_text()


def absent_keywords(count: int, prefix: str) -> list[str]:
    """Return keywords guaranteed not to occur in the text (digits never appear in it)."""
    return [f"{prefix}{index}" for index in range(count)]


def make_data_type(name: str, keywords: list[str], threshold: int = 1) -> DataType:
    """Build an unsaved keyword Data Type."""
    return DataType(id=uuid.uuid4(), name=name, type=InspectionType.KEYWORDS,
                    content=keywords, threshold=threshold)


def scenario(data_type_count: int, keywords_each: int, sensitive_every: int) -> list[DataType]:
    """Build a Data Set: many Data Types of absent keywords, some also holding a planted one.

    Args:
        data_type_count: Number of Data Types in the Data Set.
        keywords_each: Keywords per Data Type.
        sensitive_every: Every n-th Data Type also gets one planted keyword.

    Returns:
        list[DataType]: The members of the Data Set.
    """
    planted = list(PLANTED)
    data_types = []
    for index in range(data_type_count):
        keywords = absent_keywords(keywords_each, f"dt{index}k")
        if index % sensitive_every == 0:
            keywords[0] = planted[index // sensitive_every % len(planted)]
        data_types.append(make_data_type(f"Rule {index}", keywords))
    return data_types


def expected_count(keyword: str) -> int:
    """Count a planted keyword in the final text, independently of the engine.

    Uses a regex with lookarounds rather than ``str.count(" ssn ")``: the lookarounds
    check the neighbours without consuming them, so "ssn ssn" counts two, not one.
    """
    return len(re.findall(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", TEXT))


def register_input() -> None:
    """Hand the generated text to the report writer in ``conftest.py`` (once per run)."""
    if PERFORMANCE_INPUT:
        return
    first = TEXT.index(" ssn ")
    PERFORMANCE_INPUT.update(
        text=TEXT,
        planted=PLANTED,
        found={keyword: expected_count(keyword) for keyword in PLANTED},
        excerpt="..." + TEXT[max(first - 120, 0):first + 125] + "...",
    )


def describe(data_types: list[DataType], shown: int = 3) -> list[str]:
    """Summarise a Data Set for the report: its size and its first few Data Types."""
    noun = "Data Type" if len(data_types) == 1 else "Data Types"
    lines = [f"- {len(data_types)} {noun} x {len(data_types[0].content)} keywords each"]
    for data_type in data_types[:shown]:
        sample = ", ".join(f"`{keyword}`" for keyword in data_type.content[:4])
        more = len(data_type.content) - 4
        lines.append(f"- {data_type.name} (threshold {data_type.threshold}): {sample}"
                     + (f" ... and {more} more" if more > 0 else ""))
    if len(data_types) > shown:
        lines.append(f"- ... and {len(data_types) - shown} more Data Types")
    return lines


def record(scenario: str, size: str, elapsed: str, data_types: list[DataType],
           result: dict, expected: dict, response: str = "") -> None:
    """Append one scenario to the table and the optional report."""
    register_input()
    PERFORMANCE_RESULTS.append({"scenario": scenario, "size": size, "elapsed": elapsed,
                                "data_set": describe(data_types), "result": result,
                                "expected": expected, "response": response})


SCENARIOS = {
    # name: (data types, keywords each, every n-th is sensitive, budget in seconds)
    "Small policy: 1 x 5": (1, 5, 1, 0.5),
    "Department: 10 x 50": (10, 50, 2, 0.5),
    "Enterprise: 50 x 200": (50, 200, 5, 1.0),
    "Validation maximum per type: 20 x 1000": (20, 1000, 4, 1.0),
}


@pytest.mark.unit
@pytest.mark.parametrize("name", list(SCENARIOS))
def test_scan_core_on_the_largest_text(name: str) -> None:
    """The scan core stays fast and exact as the Data Set grows to 20,000 keywords."""
    data_type_count, keywords_each, sensitive_every, budget = SCENARIOS[name]
    data_types = scenario(data_type_count, keywords_each, sensitive_every)

    started = time.perf_counter()
    detections = evaluate(data_types, TEXT)
    elapsed = time.perf_counter() - started

    # Exactly the Data Types holding a planted keyword fire, each with the true count.
    expected = {
        data_type.name: expected_count(data_type.content[0])
        for data_type in data_types
        if data_type.content[0] in PLANTED
    }
    result = {d.name: d.match_count for d in detections}
    keywords = data_type_count * keywords_each
    record(name, f"{keywords:,} keywords", f"{elapsed * 1000:.1f} ms", data_types, result, expected)

    assert result == expected
    assert elapsed < budget


@pytest.mark.unit
def test_many_data_types_sharing_frequent_keywords() -> None:
    """50 Data Types that all watch the same frequent keywords: one pass serves them all."""
    data_types = [make_data_type(f"Shared {index}", list(PLANTED), threshold=500) for index in range(50)]
    total = sum(expected_count(keyword) for keyword in PLANTED)

    started = time.perf_counter()
    detections = evaluate(data_types, TEXT)
    elapsed = time.perf_counter() - started

    result = {d.name: d.match_count for d in detections}
    expected = {data_type.name: total for data_type in data_types}
    record("Shared: 50 x 4 frequent", f"{total:,} hits per type", f"{elapsed * 1000:.1f} ms",
           data_types, result, expected)

    assert result == expected
    assert elapsed < 1.0


@pytest.mark.integration
async def test_end_to_end_scan_of_the_largest_text(client: AsyncClient) -> None:
    """Through HTTP and PostgreSQL: 5 Data Sets over 40 shared Data Types, 1 MB scanned each."""
    planted = list(PLANTED)
    data_type_ids, data_types = [], []
    for index in range(40):
        keywords = absent_keywords(200, f"api{index}k")
        if index % 10 == 0:
            keywords[0] = planted[index // 10]
        body = await create_data_type(client, name=f"API rule {index}", content=keywords, threshold=1)
        data_type_ids.append(body["id"])
        data_types.append(make_data_type(body["name"], keywords))

    # Five overlapping Data Sets: each reuses 20 of the 40 Data Types.
    data_sets = [
        await create_data_set(client, *data_type_ids[start:start + 20], name=f"Set {start}")
        for start in range(0, 25, 5)
    ]

    timings, bodies = [], []
    for data_set in data_sets:
        started = time.perf_counter()
        response = await client.post("/api/scan", json={"text": TEXT, "data_set_id": data_set["id"]})
        timings.append(time.perf_counter() - started)
        assert response.status_code == 200
        bodies.append(response.json())

    # Report the first Data Set (members 0-19): its planted members are rules 0 and 10.
    first = bodies[0]
    result = {d["name"]: d["match_count"] for d in first["detected_objects"]}
    expected = {data_type.name: expected_count(data_type.content[0])
                for data_type in data_types[:20] if data_type.content[0] in PLANTED}
    shown = {**first, "detected_objects": [{**d, "id": d["id"][:8] + "..."} for d in first["detected_objects"]]}
    record("End to end: 5 Data Sets x 20 types x 200", "HTTP + DB load + scan",
           f"{max(timings) * 1000:.1f} ms (slowest)", data_types[:20], result, expected,
           response=json.dumps(shown, indent=2))

    assert all(body["status"] == "match" for body in bodies)
    assert result == expected
    assert max(timings) < 2.0
