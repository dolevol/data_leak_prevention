"""Unit tests for ``app.engines`` - the mechanics of counting.

An engine sees only patterns and a text, so these tests need no Data Type, no
database and no HTTP layer. They cover the word-boundary edge cases that are
hard to read from an API-level assertion.
"""

import pytest

from app.engines import KeywordEngine

# Pure logic: no database, no HTTP client. Runs without Docker.
pytestmark = pytest.mark.unit


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
    assert KeywordEngine().find([keyword], text) == {keyword: expected}


def test_symbol_keyword_is_not_swallowed_by_a_longer_token() -> None:
    """A keyword ending in a symbol matches standalone, not inside a longer token.

    Comparing the neighbouring characters handles this directly: after "c++" in
    "c++11" comes a digit, which extends the token, so the hit is rejected.
    """
    assert KeywordEngine().find(["c++"], "c++ is great") == {"c++": 1}
    assert KeywordEngine().find(["c++"], "c++11 only") == {"c++": 0}


def test_punctuation_in_a_keyword_is_literal() -> None:
    """A dot in a keyword is a literal dot, never a wildcard."""
    assert KeywordEngine().find(["a.b"], "a.b") == {"a.b": 1}
    assert KeywordEngine().find(["a.b"], "axb") == {"a.b": 0}


def test_keyword_that_only_starts_a_longer_word_is_not_counted() -> None:
    """"pass" occurs inside "passport", but "p" follows it, so only "passport" counts."""
    assert KeywordEngine().find(["pass", "passport"], "passport") == {"pass": 0, "passport": 1}


def test_overlapping_keywords_are_each_counted() -> None:
    """A keyword contained in another one is still counted on its own."""
    engine = KeywordEngine()
    assert engine.find(["york"], "in new york") == {"york": 1}
    assert engine.find(["new york", "york"], "in new york") == {"new york": 1, "york": 1}
    # An occurrence may also overlap the next occurrence of the same keyword.
    assert engine.find(["a a"], "a a a") == {"a a": 2}


def test_keyword_found_only_mid_word_is_rejected() -> None:
    """The automaton finds "ssn" inside "passn"; the boundary check discards it."""
    assert KeywordEngine().find(["pass", "ssn"], "my passn") == {"pass": 0, "ssn": 0}


def test_non_ascii_keyword_is_matched_by_character_position() -> None:
    """Positions are counted in characters, so text beyond ASCII keeps its boundaries."""
    assert KeywordEngine().find(["סוד"], "זה סוד, לא סודי") == {"סוד": 1}


def test_no_keywords_finds_nothing() -> None:
    """An empty pattern list is not an error for the engine, it simply finds nothing."""
    assert KeywordEngine().find([], "ssn") == {}


def test_each_spelling_gets_the_count_of_its_keyword() -> None:
    """"SSN" and "ssn" are one keyword, and both spellings are answered.

    The policy looks counts up with each Data Type's own ``content``, so a
    spelling missing from the result would be a KeyError there.
    """
    assert KeywordEngine().find(["ssn", "SSN"], "ssn and SSN") == {"ssn": 2, "SSN": 2}
