"""The inspection logic - one matcher class per inspection type.

The module separates **mechanics** from **policy**:

* ``BaseMatcher`` owns the policy every engine shares - compare a count against
  the rule's own threshold - and declares the one method an engine must supply.
* A subclass per inspection type owns the mechanics: how to count occurrences.
* ``_MATCHERS`` maps an inspection type to the single instance that serves it.
* ``detect`` orchestrates: it walks a Data Set's rules and ORs the outcomes.

Adding substring, regex, pattern or LLM inspection is a subclass with one
``count`` method plus one entry in ``_MATCHERS``. Nothing else changes.

Called by:
    ``app/api/scan.py``, once per scan request.
"""

from abc import ABC, abstractmethod

from app.models import DataType, InspectionType
from app.schemas import Combine, DetectedObject


class UnsupportedInspectionTypeError(Exception):
    """Raised when a Data Type declares a type that has no matcher.

    This is an internal inconsistency, not bad client input: ``InspectionType``
    and ``_MATCHERS`` drifted apart. It is therefore left to surface as HTTP 500.
    """


class BaseMatcher(ABC):
    """Common behaviour of every inspection engine.

    The class exists to hold the one rule that is identical for all engines:
    a Data Type fires when its occurrence count reaches its own threshold. A
    subclass therefore implements ``count`` and inherits ``detect`` unchanged -
    the Template Method pattern.

    It deliberately does **not** know about Data Sets, OR/AND, or the order in
    which rules are evaluated. That orchestration belongs to ``detect`` at
    module level, so the engines stay independent of the scan flow.
    """

    @abstractmethod
    def count(self, patterns: list[str], text: str) -> int:
        """Count how many times the rule's patterns occur in the text.

        The only method an engine must supply. It returns a number rather than
        a boolean because the threshold comparison is shared policy, and because
        the number is reported back to the client as ``match_count``.

        Args:
            patterns: The Data Type's ``content``.
            text: The input to inspect.

        Returns:
            int: Number of occurrences found.
        """

    def detect(self, data_type: DataType, text: str) -> DetectedObject | None:
        """Compare one Data Type's occurrence count against its own threshold.

        Shared by every engine, which is what justifies the base class.

        Args:
            data_type: The rule to evaluate.
            text: The input to inspect.

        Returns:
            DetectedObject | None: The detection when the threshold is reached,
                otherwise None.
        """
        occurrences = self.count(data_type.content, text)
        if occurrences < data_type.threshold:
            return None
        return DetectedObject(id=data_type.id, name=data_type.name, match_count=occurrences)


class KeywordMatcher(BaseMatcher):
    """Counts whole-word occurrences of a list of keywords.

    Implemented with plain string comparison rather than a compiled pattern,
    which removes three problems instead of defending against them: there are
    no metacharacters to escape, no ``\\b`` to mishandle a keyword ending in a
    symbol, and no pattern compiled from user input to become a ReDoS vector.
    """

    def _is_standalone(self, text: str, start: int, end: int) -> bool:
        """Report whether the slice ``text[start:end]`` stands as a whole word.

        This is why counting cannot simply be ``text.count(keyword)``: ``count``
        finds substrings, so it would report a hit for "pass" inside "password".

        A character extends a word only if it is a letter or a digit.
        The underscore is deliberately excluded, which is where this departs
        from a regex ``\\w``: for a DLP service snake_case is a separator, and
        missing ``ssn`` inside ``ssn_number`` would be a detection failure.

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

    def _count_one(self, keyword: str, text: str) -> int:
        """Count the standalone occurrences of a single keyword.

        Args:
            keyword: The lower-cased keyword to look for.
            text: The lower-cased text to inspect.

        Returns:
            int: Number of standalone occurrences.
        """
        if not keyword:
            return 0
        occurrences = 0
        start = text.find(keyword)
        while start != -1:
            if self._is_standalone(text, start, start + len(keyword)):
                occurrences += 1
            # Resume one character later so overlapping occurrences are not skipped.
            start = text.find(keyword, start + 1)
        return occurrences

    def count(self, patterns: list[str], text: str) -> int:
        """Count whole-word keyword occurrences, repetitions included.

        Counting total occurrences rather than distinct keywords matches the
        "minimum occurrences" semantics of commercial DLP dictionaries, and keeps
        the reported ``match_count`` identical to the number the threshold was
        compared against. Each keyword is counted independently.

        Args:
            patterns: The Data Type's keywords.
            text: The input to inspect.

        Returns:
            int: Total number of whole-word occurrences.
        """
        # Lower-casing both sides once is the whole of the case-insensitivity rule.
        lowered_text = text.lower()
        return sum(self._count_one(keyword.lower(), lowered_text) for keyword in patterns)


# Fits each InspectionType to its Matcher constructor (and logic)
_MATCHERS: dict[InspectionType, BaseMatcher] = {
    InspectionType.KEYWORDS: KeywordMatcher(),
}


def _matcher_for(data_type: DataType) -> BaseMatcher:
    """Return the matcher registered for a Data Type's inspection type.

    Dispatching per Data Type means a single Data Set may mix keyword rules with
    any engine added later.

    Args:
        data_type: The rule whose type selects the matcher.

    Returns:
        BaseMatcher: The engine that evaluates this rule.

    Raises:
        UnsupportedInspectionTypeError: If the type has no registered matcher.
    """
    matcher = _MATCHERS.get(data_type.type)
    if matcher is None:
        raise UnsupportedInspectionTypeError(
            f"no matcher registered for inspection type '{data_type.type}'"
        )
    return matcher


def detect(
    data_types: list[DataType], text: str, combine: Combine = Combine.OR
) -> list[DetectedObject]:
    """Evaluate every Data Type against the text and collect what fired.

    This is the entry point of the module and the only function the API layer
    calls. It is a plain function, not a method: walking a Data Set's rules is
    orchestration, not the business of any single engine. An empty list always
    means "not matched".

    Args:
        data_types: The rules of the Data Set being scanned.
        text: The input to inspect.
        combine: How the outcomes form the verdict. Only OR is implemented;
            the argument keeps the decision an open one rather than a
            hard-coded rule.

    Returns:
        list[DetectedObject]: The Data Types that matched, or an empty list.
    """
    return [
        detection
        for data_type in data_types
        if (detection := _matcher_for(data_type).detect(data_type, text)) is not None
    ]
