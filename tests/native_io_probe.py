"""Exercise the native reader in an isolated process for crash detection."""

from __future__ import annotations

import sys

from gambit import _io


def main() -> None:
    for filename in sys.argv[1:]:
        try:
            _io.read_file(filename, [0, 1], ["S32", "f8"], ",", 0, 0)
        except (RuntimeError, TypeError, ValueError):
            pass


if __name__ == "__main__":
    main()
