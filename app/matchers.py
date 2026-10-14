"""The inspection logic - one matcher class per inspection type.

The module separates **mechanics** from **policy**:

* ``BaseMatcher`` owns the policy every engine shares - compare a count against
  the rule's own threshold - and declares the one method an engine must supply.
* A subclass per inspection type owns the mechanics: how to count occurrences.
* ``_MATCHERS`` maps an inspection type to the single instance that serves it.
* ``detect`` orchestrates: it walks a Data Set's rules and ORs the outcomes.
  It also owns the one ``memo`` of a scan, and first lets every engine
  ``prepare`` with all of its patterns, so an engine can read the text once
  for the whole Data Set instead of once per Data Type.

Adding substring, regex, pattern or LLM inspection is a subclass with one
``count`` method plus one entry in ``_MATCHERS``. Nothing else changes;
overriding ``prepare`` is an optimisation, never a requirement.

Called by:
    ``app/api/scan.py``, once per scan request.
"""

from abc import ABC, abstractmethod
from typing import Any

import ahocorasick

from app.models import DataType, InspectionType
from app.schemas import Combine, DetectedObject


class UnsupportedInspectionTypeError(Exception):
    """Raised when a Data Type declares a type that has no matcher.

    This is an internal inconsistency, not bad client input: ``InspectionType``
    and ``_MATCHERS`` drifted apart. It is therefore left to surface as HTTP 500.
    """


# Scratch space that lives for exactly one scan. Each engine keeps its own state
# under its own instance as the key, so engines cannot overwrite one another. It
# is passed in rather than stored on the matcher because the matchers are shared
# singletons: state on ``self`` would leak from one request into the next.
ScanMemo = dict["BaseMatcher", Any]


class BaseMatcher(ABC):
    """Common behaviour of every inspection engine.

    The class exists to hold the one rule that is identical for all engines:
    a Data Type fires when its occurrence count reaches its own threshold. A
    subclass therefore implements ``count`` and inherits ``detect`` unchanged -
    the Template Method pattern. ``prepare`` is an optional second hook.

    It deliberately does **not** know about Data Sets, OR/AND, or the order in
    which rules are evaluated. That orchestration belongs to ``detect`` at
    module level, so the engines stay independent of the scan flow.
    """

    def prepare(self, patterns: list[str], text: str, memo: ScanMemo) -> None:
        """Do, once per scan, the work that does not depend on a single Data Type.

        Called by the module-level ``detect`` before any ``count``, with the
        patterns of every Data Type this engine will be asked about. An engine
        that gains from seeing them all together stores its result in the memo;
        the default does nothing, and ``count`` then works alone.

        Args:
            patterns: The ``content`` of all the engine's Data Types, joined.
            text: The input to inspect.
            memo: The scan's scratch space.
        """

    @abstractmethod
    def count(self, patterns: list[str], text: str, memo: ScanMemo | None = None) -> int:
        """Count how many times the rule's patterns occur in the text.

        The only method an engine must supply. It returns a number rather than
        a boolean because the threshold comparison is shared policy, and because
        the number is reported back to the client as ``match_count``.

        Args:
            patterns: The Data Type's ``content``.
            text: The input to inspect.
            memo: The scan's scratch space, holding whatever ``prepare`` left
                there. None means "no scan to share with", and must give the
                same result.

        Returns:
            int: Number of occurrences found.
        """

    def detect(
        self, data_type: DataType, text: str, memo: ScanMemo | None = None
    ) -> DetectedObject | None:
        """Compare one Data Type's occurrence count against its own threshold.

        Shared by every engine, which is what justifies the base class.

        Args:
            data_type: The rule to evaluate.
            text: The input to inspect.
            memo: The scan's scratch space, handed on to ``count`` untouched.

        Returns:
            DetectedObject | None: The detection when the threshold is reached,
                otherwise None.
        """
        occurrences = self.count(data_type.content, text, memo)
        if occurrences < data_type.threshold:
            return None
        return DetectedObject(id=data_type.id, name=data_type.name, match_count=occurrences)


class KeywordMatcher(BaseMatcher):
    """Counts whole-word occurrences of a list of keywords.

    The search is split in two. An Aho-Corasick automaton finds every place a
    keyword occurs as a substring, for all keywords in a single pass over the
    text; ``_is_standalone`` then decides which of those are whole words.

    Both halves compare plain characters rather than run a compiled pattern,
    which removes three problems instead of defending against them: there are
    no metacharacters to escape, no ``\\b`` to mishandle a keyword ending in a
    symbol, and no pattern compiled from user input to become a ReDoS vector.
    """

    def _is_standalone(self, text: str, start: int, end: int) -> bool:
        """Report whether the slice ``text[start:end]`` stands as a whole word.

        This is why the automaton's hits cannot be counted as they are: it
        finds substrings, so it reports a hit for "pass" inside "password".

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

    def _count_all(self, patterns: list[str], text: str) -> dict[str, int]:
        """Count the standalone occurrences of every keyword in one pass.

        The automaton is a tree of all the keywords, walked one character of the
        text at a time, so the cost depends on the length of the text and on the
        number of hits - not on how many keywords there are. Building it takes
        well under a millisecond, so it is rebuilt per scan and never cached.

        Args:
            patterns: Keywords in any case; repetitions are searched for once.
            text: The input to inspect, in its original case.

        Returns:
            dict[str, int]: Standalone occurrences, keyed by lower-cased keyword.
        """
        # Lower-casing both sides once is the whole of the case-insensitivity rule.
        lowered_text = text.lower()
        counts = dict.fromkeys((keyword.lower() for keyword in patterns), 0)

        automaton = ahocorasick.Automaton()
        for keyword in counts:
            # The schemas reject a blank keyword; one that slipped past them counts zero.
            if keyword:
                automaton.add_word(keyword, keyword)
        # The library refuses to search an automaton that holds no words.
        if len(automaton) == 0:
            return counts
        automaton.make_automaton()

        # Each hit is the index of the keyword's last character plus the keyword
        # itself. Overlapping and nested keywords are all reported.
        for last, keyword in automaton.iter(lowered_text):
            end = last + 1
            if self._is_standalone(lowered_text, end - len(keyword), end):
                counts[keyword] += 1
        return counts

    def prepare(self, patterns: list[str], text: str, memo: ScanMemo) -> None:
        """Count the keywords of all Data Types together, in a single pass.

        Args:
            patterns: Every keyword of every keyword Data Type in the scan.
            text: The input to inspect.
            memo: The scan's scratch space; the counts are left under ``self``.
        """
        memo[self] = self._count_all(patterns, text)

    def count(self, patterns: list[str], text: str, memo: ScanMemo | None = None) -> int:
        """Count whole-word keyword occurrences, repetitions included.

        Counting total occurrences rather than distinct keywords matches the
        "minimum occurrences" semantics of commercial DLP dictionaries, and keeps
        the reported ``match_count`` identical to the number the threshold was
        compared against. Each keyword is counted independently.

        Inside a scan the text was already read by ``prepare``, so this only
        adds up the counts of this Data Type's keywords. The result is identical
        either way.

        Args:
            patterns: The Data Type's keywords.
            text: The input to inspect.
            memo: The scan's scratch space. None, as in a direct call from a unit
                test, means there is no prepared scan and the text is read here.

        Returns:
            int: Total number of whole-word occurrences.
        """
        counts = None if memo is None else memo.get(self)
        if counts is None:
            counts = self._count_all(patterns, text)
        return sum(counts[keyword.lower()] for keyword in patterns)


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
    # One memo per scan: created here, dropped when the scan returns.
    memo: ScanMemo = {}

    # Hand each engine all of its patterns at once, before any Data Type is judged.
    patterns: dict[BaseMatcher, list[str]] = {}
    for data_type in data_types:
        patterns.setdefault(_matcher_for(data_type), []).extend(data_type.content)
    for matcher, matcher_patterns in patterns.items():
        matcher.prepare(matcher_patterns, text, memo)

    return [
        detection
        for data_type in data_types
        if (detection := _matcher_for(data_type).detect(data_type, text, memo)) is not None
    ]
