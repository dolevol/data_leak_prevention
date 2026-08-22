"""The inspection logic - counts rule occurrences and combines the results.

The whole matching logic of the service lives in this single module, as plain
functions:

* a counting function per inspection logic (currently only keywords),
* ``_COUNTERS``, mapping an inspection type to its counting function,
* ``detect``, which applies each rule's threshold and combines the outcomes.

Adding substring, regex, pattern or LLM inspection is two lines: a counting
function with the signature ``(patterns, text) -> int``, and one entry in
``_COUNTERS``. Nothing else in the service changes.

Called by:
    ``app/api/scan.py``, once per scan request.
"""

from collections.abc import Callable

from app.models import DataType, InspectionType
from app.schemas import Combine, DetectedObject


class UnsupportedInspectionTypeError(Exception):
    """Raised when a Data Type declares a type that has no counting function.

    This is an internal inconsistency, not bad client input: ``InspectionType``
    and ``_COUNTERS`` drifted apart. It is therefore left to surface as HTTP 500.
    """


def _is_standalone(text: str, start: int, end: int) -> bool:
    """Report whether the slice ``text[start:end]`` stands as a whole word.

    This is the whole-word rule of the specification, and it is why counting
    cannot simply be ``text.count(keyword)``: ``count`` finds substrings, so it
    would report a hit for "pass" inside "password". An occurrence is a real
    word only when nothing extends it on either side.

    A character extends a word only if it is a letter or a digit.
    ``str.isalnum`` is Unicode aware, so Hebrew and accented letters count
    exactly like ASCII ones.

    The underscore is deliberately **not** treated as a word character, which is
    where this differs from a regex ``\\b``. In the material a DLP service
    inspects, snake_case is a separator: ``ssn_number``, ``passport_id`` and
    ``credit_card`` are exactly the field names sensitive data hides behind, and
    an engine that fails to see ``ssn`` inside ``ssn_number`` is failing at its
    job. Letters are different - ``password`` is a genuinely different word from
    ``pass``, not the same word with a separator.

    Args:
        text: The text the occurrence was found in.
        start: Index of the first character of the occurrence.
        end: Index just past its last character.

    Returns:
        bool: True when nothing extends the occurrence into a longer word.
    """
    # A space stands in for "past the edge of the text": it is not a word
    # character, so running off either end counts as a boundary automatically.
    before = text[start - 1] if start > 0 else " "
    after = text[end] if end < len(text) else " "
    return not before.isalnum() and not after.isalnum()


def _count_keyword(keyword: str, text: str) -> int:
    """Count the standalone occurrences of one keyword.

    ``str.find`` walks the text from left to right; each hit is kept only if
    ``_is_standalone`` accepts it. Both arguments are expected to be lower-cased
    already, which is how case-insensitivity is achieved without a regex flag.

    Args:
        keyword: The lower-cased keyword to look for.
        text: The lower-cased text to inspect.

    Returns:
        int: Number of standalone occurrences.
    """
    # str.find("") reports a hit at every position.
    if not keyword:
        return 0
    count = 0
    start = text.find(keyword)
    while start != -1:
        if _is_standalone(text, start, start + len(keyword)):
            count += 1
        # Resume one character later so overlapping occurrences are not skipped.
        start = text.find(keyword, start + 1)
    return count


def _count_keywords(patterns: list[str], text: str) -> int:
    """Count whole-word keyword occurrences, repetitions included.

    Counting total occurrences rather than distinct keywords
    because it matches the "minimum occurrences" semantics of
    commercial DLP dictionaries, and keeps the reported ``match_count``
    identical to the number the threshold was compared against.

    Each keyword is counted independently. Two keywords that overlap on the same
    span - "credit" and "credit card" over "my credit card" - therefore
    contribute one occurrence each. A keyword that is only a prefix of a longer
    one is still not double counted, because the boundary check rejects it:
    "pass" inside "passport" is glued to a word character.

    Args:
        patterns: The Data Type's ``content`` - the keywords to look for.
        text: The input to inspect.

    Returns:
        int: Total number of whole-word occurrences.
    """
    # Lower-casing both sides once is the whole of the case-insensitivity rule.
    lowered_text = text.lower()
    return sum(_count_keyword(keyword.lower(), lowered_text) for keyword in patterns)


# The extension point: one entry per inspection logic
_COUNTERS: dict[InspectionType, Callable[[list[str], str], int]] = {
    InspectionType.KEYWORDS: _count_keywords,
}


def _count(data_type: DataType, text: str) -> int:
    """Run the counting function registered for a rule's inspection type.

    Dispatching on the Data Type's own type means a single Data Set may mix
    keyword rules with any engine added later.

    Args:
        data_type: The rule whose type selects the counting function.
        text: The input to inspect.

    Returns:
        int: Number of occurrences found.

    Raises:
        UnsupportedInspectionTypeError: If the type has no counting function.
    """
    counter = _COUNTERS.get(data_type.type)
    if counter is None:
        raise UnsupportedInspectionTypeError(
            f"no counting function registered for inspection type '{data_type.type}'"
        )
    return counter(data_type.content, text)


def _detect_one(data_type: DataType, text: str) -> DetectedObject | None:
    """Compare one Data Type's occurrence count against its own threshold.

    Args:
        data_type: The rule to evaluate.
        text: The input to inspect.

    Returns:
        DetectedObject | None: The detection when the threshold is reached,
            otherwise None.
    """
    count = _count(data_type, text)
    if count < data_type.threshold:
        return None
    return DetectedObject(id=data_type.id, name=data_type.name, match_count=count)


def detect(
    data_types: list[DataType], text: str, combine: Combine = Combine.OR
) -> list[DetectedObject]:
    """Evaluate every Data Type against the text and combine the outcomes.

    This is the entry point of the module and the only function the API layer
    calls. An empty list always means "not matched", whichever combine mode is
    in use, so the caller never has to know which mode was chosen.

    Args:
        data_types: The rules of the Data Set being scanned.
        text: The input to inspect.
        combine: OR (the default, as the specification requires) matches when at
            least one Data Type reached its threshold. 
            Passing it per call is what keeps the rule an open argument
            rather than a hard-coded one.

    Returns:
        list[DetectedObject]: The Data Types that matched, or an empty list.
    """
    detections = [
        detection
        for data_type in data_types
        if (detection := _detect_one(data_type, text)) is not None
    ]
    return detections
