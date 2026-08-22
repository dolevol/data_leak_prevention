"""Unit tests for the matching engine.

These tests touch no database and no HTTP layer, which is the point: the
matching logic is decoupled enough to be exercised on its own. They cover the
word-boundary edge cases that are hard to read from an API-level assertion, and
the OR combination across Data Types.
"""

import uuid

import pytest

from app.matchers import UnsupportedInspectionTypeError, _count_keywords, detect
from app.models import DataType, InspectionType


def make_data_type(*keywords: str, name: str = "test", threshold: int = 1) -> DataType:
    """Build an unsaved Data Type for the engine to evaluate.

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


@pytest.mark.parametrize(
    ("keyword", "text", "expected"),
    [
        ("pass", "pass", 1),                      # standalone word
        ("pass", "password", 0),                  # keyword contained in a longer word
        ("pass", "bypass", 0),                    # keyword used as a suffix
        ("pass", "my_pass", 1),                   # underscore separates, it does not glue
        ("pass", "(pass), pass.", 2),             # punctuation still delimits words
        ("pass", "PASS Pass pass", 3),            # matching is case-insensitive
        ("credit card", "my credit card no", 1),  # a keyword may contain a space
    ],
)
def test_whole_word_matching(keyword: str, text: str, expected: int) -> None:
    """Only standalone occurrences of a keyword are counted."""
    assert _count_keywords([keyword], text) == expected


def test_symbol_keyword_is_not_swallowed_by_a_longer_token() -> None:
    """A keyword ending in a symbol matches standalone, not inside a longer token.

    Comparing the neighbouring characters handles this directly: after "c++" in
    "c++11" comes a digit, which extends the token, so the hit is rejected.
    """
    assert _count_keywords(["c++"], "c++ is great") == 1
    assert _count_keywords(["c++"], "c++11 only") == 0


def test_punctuation_in_a_keyword_is_literal() -> None:
    """A dot in a keyword is a literal dot, never a wildcard."""
    assert _count_keywords(["a.b"], "a.b") == 1
    assert _count_keywords(["a.b"], "axb") == 0


def test_counts_total_occurrences_not_distinct_keywords() -> None:
    """Repetitions count"""
    assert _count_keywords(["ssn", "passport", "visa"], "passport, passport, passport") == 3


def test_longer_keyword_wins_over_its_prefix() -> None:
    """Overlapping keywords are counted once, preferring the longer one."""
    assert _count_keywords(["pass", "passport"], "passport") == 1


def test_threshold_is_applied_per_data_type() -> None:
    """A rule fires only once its own threshold is reached."""
    data_type = make_data_type("ssn", threshold=2)
    assert detect([data_type], "ssn") == []
    assert len(detect([data_type], "ssn and ssn")) == 1


def test_detection_reports_id_name_and_count() -> None:
    """A detection carries everything the caller needs without a second lookup."""
    data_type = make_data_type("ssn", name="Identifiers", threshold=1)

    detection = detect([data_type], "ssn ssn")[0]
    assert detection.id == data_type.id
    assert detection.name == "Identifiers"
    assert detection.match_count == 2


def test_matches_when_a_single_rule_fires() -> None:
    """A Data Set matches when at least one rule fires, and reports only that rule."""
    fires = make_data_type("salary", name="Salary")
    silent = make_data_type("ssn", name="Identifiers")

    detections = detect([fires, silent], "my salary")
    assert [detection.name for detection in detections] == ["Salary"]


def test_reports_every_rule_that_fires() -> None:
    """More than one rule can fire on the same text, and both are reported."""
    first = make_data_type("salary", name="Salary")
    second = make_data_type("ssn", name="Identifiers")

    detections = detect([first, second], "my salary and ssn")
    assert sorted(detection.name for detection in detections) == ["Identifiers", "Salary"]


def test_unregistered_inspection_type_is_rejected() -> None:
    """An inspection type with no counting function is an internal inconsistency."""
    data_type = make_data_type("ssn")
    # Bypasses validation on purpose: only a bug could produce this state.
    data_type.type = "substring"

    with pytest.raises(UnsupportedInspectionTypeError):
        detect([data_type], "ssn")
