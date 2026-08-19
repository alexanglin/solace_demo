"""Gate entry point: run the checker's own conformance suite, then scan the tree.

The conformance suite runs first and the gate fails closed if it breaks, so the checker
can never report a clean tree because the checker itself is broken.
"""

from __future__ import annotations

import sys
import unittest
from collections.abc import Callable, Sequence

from tools.aaa_checker.checker import main as check_main


def run_self_tests() -> bool:
    """Run the checker's positive and negative conformance suite."""
    suite = unittest.defaultTestLoader.discover(
        "tools/aaa_checker/tests",
        pattern="test_*.py",
    )
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=1).run(suite)
    return result.wasSuccessful()


def main(
    argv: Sequence[str] | None = None,
    *,
    checker_main: Callable[[Sequence[str] | None], int] = check_main,
) -> int:
    """Return 2 when the conformance suite fails, otherwise the checker's exit code."""
    if not run_self_tests():
        return 2
    return checker_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
