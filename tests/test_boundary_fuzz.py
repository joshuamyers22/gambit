from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_seeded_boundary_fuzz_corpus_completes_without_crash() -> None:
    probe = Path(__file__).with_name("boundary_fuzz_probe.py")
    result = subprocess.run(
        [sys.executable, str(probe)],
        check=False,
        capture_output=os.environ.get("GAMBIT_SANITIZER_RUN") != "1",
        timeout=30,
    )
    assert result.returncode == 0, (
        "boundary fuzz probe terminated unexpectedly: "
        f"stderr={(result.stderr or b'').decode(errors='replace')}"
    )
