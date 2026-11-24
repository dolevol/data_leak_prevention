"""Root-level pytest configuration: command-line options only.

Options must be registered here, at the root, rather than in ``tests/conftest.py``.
pytest loads a conftest early only if it sits on the path of a command-line
argument. In ``pytest -m performance --perf-report perf_report`` the value
``perf_report`` is first read as a path to test; once that folder exists,
``tests/conftest.py`` is no longer on any argument's path, the option is
unknown, and pytest exits with "unrecognized arguments". The root is on every
path, so an option registered here is always known.
"""


def pytest_addoption(parser) -> None:
    """Add ``--perf-report DIR``: write the performance input and output to files.

    A pytest hook: pytest calls it once at startup, before collecting tests.
    The report itself is written by ``pytest_terminal_summary`` in ``tests/conftest.py``.

    Args:
        parser: pytest's command-line parser.
    """
    parser.addoption(
        "--perf-report",
        metavar="DIR",
        # Given alone, the flag writes next to the tests; a DIR after it overrides that.
        nargs="?",
        const="tests/perf_report",
        help="write the generated scan text and a per-scenario input/output report "
        "to DIR (default: tests/perf_report)",
    )
