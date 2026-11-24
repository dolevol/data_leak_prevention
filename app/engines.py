"""Inspection engines - the MECHANICS of a scan: how often does each pattern occur?

An engine answers exactly one question: given some patterns and a text, how many
times does each pattern occur? It never sees a Data Type, a threshold or a Data
Set. Deciding which Data Types fired is policy, and lives in ``app.policy``.

That split is what keeps an engine simple:

* It is a pure function wrapped in a class: the same input always gives the same
  output, and nothing is stored on ``self``. One shared instance per type is
  therefore safe across concurrent requests.
* It receives the patterns of *every* Data Type of its type at once, so it can
  read the text a single time per scan, however many Data Types there are.

Adding an inspection type (regex, LLM, ...) is one ``Engine`` subclass plus one
entry in ``ENGINES``. Nothing in ``app.policy`` changes.

Called by:
    ``app.policy.evaluate``, once per engine type per scan.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterable

import ahocorasick

from app.models import InspectionType


class UnsupportedInspectionTypeError(Exception):
    """Raised when a Data Type declares a type that has no engine.

    This is an internal inconsistency, not bad client input: ``InspectionType``
    and ``ENGINES`` drifted apart. It is therefore left to surface as HTTP 500.
    """


class Engine(ABC):
    """The contract every inspection engine fulfils.

    The class exists so that ``app.policy`` can call every engine the same way,
    without knowing which one it is (the Strategy pattern).
    """

    @abstractmethod
    def find(self, patterns: Iterable[str], text: str) -> dict[str, int]:
        """Read the text and count the occurrences of every pattern.

        Args:
            patterns: The patterns to look for, from every Data Type of this type.
            text: The input to inspect.

        Returns:
            dict[str, int]: One entry per given pattern, spelled exactly as given,
                holding its number of occurrences (zero when absent).
        """


class KeywordEngine(Engine):
    """Counts whole-word, case-insensitive occurrences of keywords.

    The search is split in two. An Aho-Corasick automaton finds every place a
    keyword occurs as a substring, for all keywords in a single pass over the
    text; ``_is_standalone`` then decides which of those are whole words.

    Both halves compare plain characters rather than run a compiled pattern,
    which removes three problems instead of defending against them: there are
    no metacharacters to escape, no ``\\b`` to mishandle a keyword ending in a
    symbol, and no pattern compiled from user input to become a ReDoS vector.
    """

    def find(self, patterns: Iterable[str], text: str) -> dict[str, int]:
        """Count the standalone occurrences of every keyword in one pass.

        The automaton is a tree of all the keywords, walked one character of the
        text at a time, so the cost depends on the length of the text and on the
        number of hits - not on how many keywords there are. Building it takes
        well under a millisecond, so it is rebuilt per scan and never cached.

        Args:
            patterns: Keywords in any case. "SSN" and "ssn" are one keyword, and
                both spellings get the same count back.
            text: The input to inspect, in its original case.

        Returns:
            dict[str, int]: Standalone occurrences, keyed by each keyword as given.
        """
        patterns = list(patterns)
        # Lower-casing both sides once is the whole of the case-insensitivity rule.
        lowered_text = text.lower()
        counts = dict.fromkeys((keyword.lower() for keyword in patterns), 0)

        automaton = ahocorasick.Automaton()
        for keyword in counts:
            # The schemas reject a blank keyword; one that slipped past them counts zero.
            if keyword:
                automaton.add_word(keyword, keyword)

        # The library refuses to search an automaton that holds no words.
        if len(automaton) > 0:
            automaton.make_automaton()
            # Each hit is the index of the keyword's last character plus the keyword
            # itself. Overlapping and nested keywords are all reported.
            for last, keyword in automaton.iter(lowered_text):
                end = last + 1
                if self._is_standalone(lowered_text, end - len(keyword), end):
                    counts[keyword] += 1

        # Hand the counts back under the spelling the caller used, so the policy
        # can look them up with a Data Type's own ``content``.
        return {pattern: counts[pattern.lower()] for pattern in patterns}

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


# The registry: which engine serves which inspection type. A plain dict is enough -
# every engine is known when the code is written, and each holds no state.
ENGINES: dict[InspectionType, Engine] = {
    InspectionType.KEYWORDS: KeywordEngine(),
}


def engine_for(inspection_type: InspectionType) -> Engine:
    """Return the engine registered for an inspection type.

    Callers go through this function rather than the dict, so a missing entry
    becomes a named error instead of a bare ``KeyError``.

    Args:
        inspection_type: The ``type`` of a Data Type.

    Returns:
        Engine: The engine that counts patterns of this type.

    Raises:
        UnsupportedInspectionTypeError: If the type has no registered engine.
    """
    engine = ENGINES.get(inspection_type)
    if engine is None:
        raise UnsupportedInspectionTypeError(
            f"no engine registered for inspection type '{inspection_type}'"
        )
    return engine
