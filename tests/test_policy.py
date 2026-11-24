"""Unit tests for ``app.policy`` - which Data Types fired.

The Data Types here are in-memory ORM objects that are never saved, so these
tests need no database. They cover the rules of the specification: per-Data-Type
thresholds, total occurrences, OR across Data Types, and one pass per scan.
"""

import uuid

import pytest

from app.engines import KeywordEngine, UnsupportedInspectionTypeError
from app.models import DataType, InspectionType
from app.policy import evaluate

# Pure logic: no database, no HTTP client. Runs without Docker.
pytestmark = pytest.mark.unit


def make_data_type(*keywords: str, name: str = "test", threshold: int = 1) -> DataType:
    """Build an unsaved Data Type for the policy to evaluate.

    Args:
        *keywords: The keywords the rule looks for.
        name: The rule's name, reported back in a detection.
        threshold: Occurrences required for the rule to fire.

    Returns:
        DataType: An in-memory ORM object, never added to a session.
    """
    return DataType(
        # The id is normally generated on INSERT, which never happens here.
        id=uuid.uuid4(),
        name=name,
        type=InspectionType.KEYWORDS,
        content=list(keywords),
        threshold=threshold,
    )


def test_counts_total_occurrences_not_distinct_keywords() -> None:
    """Repetitions count: one keyword three times is a count of three."""
    data_type = make_data_type("ssn", "passport", "visa", threshold=3)
    assert evaluate([data_type], "passport, passport, passport")[0].match_count == 3


def test_threshold_is_applied_per_data_type() -> None:
    """A rule fires only once its own threshold is reached."""
    data_type = make_data_type("ssn", threshold=2)
    assert evaluate([data_type], "ssn") == []
    assert len(evaluate([data_type], "ssn and ssn")) == 1


def test_detection_reports_id_name_and_count() -> None:
    """A detection carries everything the caller needs without a second lookup."""
    data_type = make_data_type("ssn", name="Identifiers", threshold=1)
    detection = evaluate([data_type], "ssn ssn")[0]
    assert detection.id == data_type.id
    assert detection.name == "Identifiers"
    assert detection.match_count == 2


def test_the_text_is_read_once_for_the_whole_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    """All rules of a scan share one engine call, yet each reports its own count."""
    calls = []
    original = KeywordEngine.find

    def spy(self: KeywordEngine, patterns, text: str) -> dict[str, int]:
        calls.append(set(patterns))
        return original(self, patterns, text)

    monkeypatch.setattr(KeywordEngine, "find", spy)

    first = make_data_type("ssn", "visa", name="First")
    # "SSN" differs only in case, so it is the same keyword as far as matching goes.
    second = make_data_type("SSN", name="Second", threshold=2)
    detections = evaluate([first, second], "ssn and SSN")

    assert {detection.name: detection.match_count for detection in detections} == {
        "First": 2,
        "Second": 2,
    }
    assert calls == [{"ssn", "visa", "SSN"}]


def test_counts_do_not_leak_between_scans() -> None:
    """The engines hold no state, so one text's counts never reach the next scan."""
    data_type = make_data_type("ssn")
    assert evaluate([data_type], "ssn ssn")[0].match_count == 2
    assert evaluate([data_type], "nothing here") == []


def test_matches_when_a_single_rule_fires() -> None:
    """A Data Set matches when at least one rule fires, and reports only that rule."""
    fires = make_data_type("salary", name="Salary")
    silent = make_data_type("ssn", name="Identifiers")
    detections = evaluate([fires, silent], "my salary")
    assert [detection.name for detection in detections] == ["Salary"]


def test_reports_every_rule_that_fires() -> None:
    """More than one rule can fire on the same text, and both are reported."""
    first = make_data_type("salary", name="Salary")
    second = make_data_type("ssn", name="Identifiers")
    detections = evaluate([first, second], "my salary and ssn")
    assert sorted(detection.name for detection in detections) == ["Identifiers", "Salary"]


def test_unregistered_inspection_type_is_rejected() -> None:
    """An inspection type with no engine is an internal inconsistency."""
    data_type = make_data_type("ssn")
    # Bypasses validation on purpose: only a bug could produce this state.
    data_type.type = "substring"
    with pytest.raises(UnsupportedInspectionTypeError):
        evaluate([data_type], "ssn")
