"""Scan policy - the POLICY of a scan: which Data Types fired?

This module turns the raw counts that the engines produce into a verdict. It
knows the rules of the specification - each Data Type has its own threshold,
counts are never pooled across Data Types, and the outcomes are combined with
OR - and it knows nothing about how occurrences are counted.

Called by:
    ``app/api/scan.py``, once per scan request.
"""

from app.engines import engine_for
from app.models import DataType, InspectionType
from app.schemas import Combine, DetectedObject


def evaluate(
    data_types: list[DataType], text: str, combine: Combine = Combine.OR
) -> list[DetectedObject]:
    """Evaluate every Data Type of a Data Set against the text.

    Works in two steps, and the order is what keeps the scan to one pass:

    1. **Count.** Each engine is called once, with the patterns of all the Data
       Types of its type together.
    2. **Judge.** Each Data Type adds up the counts of its own patterns and
       compares the total with its own threshold.

    Args:
        data_types: The rules of the Data Set being scanned.
        text: The input to inspect.
        combine: How the outcomes form the verdict. Only OR exists today: every
            Data Type that fired is reported, and one is enough for a match. This
            argument is the single place an AND mode would be added.

    Returns:
        list[DetectedObject]: The Data Types that fired; empty means "not matched".
    """
    counts = _count(data_types, text)

    detections = []
    for data_type in data_types:
        # Repetitions count: this is the total of occurrences, not of distinct keywords.
        total = sum(counts[data_type.type][pattern] for pattern in data_type.content)
        if total >= data_type.threshold:
            detections.append(
                DetectedObject(id=data_type.id, name=data_type.name, match_count=total)
            )
    return detections


def _count(data_types: list[DataType], text: str) -> dict[InspectionType, dict[str, int]]:
    """Step 1: run each engine once, over the patterns of all its Data Types.

    Args:
        data_types: The rules of the Data Set being scanned.
        text: The input to inspect.

    Returns:
        dict[InspectionType, dict[str, int]]: For each inspection type, the
            occurrences of every pattern that any Data Type of that type uses.

    Raises:
        UnsupportedInspectionTypeError: If a Data Type has no registered engine.
    """
    # Group the patterns by inspection type. A set drops a keyword that two Data
    # Types share, so the engine looks for it once and both reuse the count.
    patterns_by_type: dict[InspectionType, set[str]] = {}
    for data_type in data_types:
        patterns_by_type.setdefault(data_type.type, set()).update(data_type.content)

    # One engine call per inspection type, with every pattern of that type at once.
    counts: dict[InspectionType, dict[str, int]] = {}
    for inspection_type, patterns in patterns_by_type.items():
        engine = engine_for(inspection_type)
        counts[inspection_type] = engine.find(patterns, text)
    return counts
