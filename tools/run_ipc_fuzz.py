"""Run bounded coverage-guided Python IPC preflight fuzzing in a child process.

No mutated IPC is sent to Polars' native decoder. Install the hash-pinned Linux
CPython 3.12 test requirements, or explicitly select seed replay without Atheris.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=10000)
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20260911)
    parser.add_argument("--replay-only", action="store_true")
    args = parser.parse_args()
    if args.runs <= 0 or args.seconds <= 0 or not 0 < args.seed < 2**32:
        parser.error("runs/seconds must be positive; seed must fit a nonzero uint32")
    sys.path.insert(0, str(ROOT / "tests"))
    from ipc_fuzz_target import MAX_INPUT_BYTES, seed_inputs

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    corpus = output / "corpus"
    corpus.mkdir()
    for index, payload in enumerate(seed_inputs()):
        (corpus / f"seed-{index:02d}").write_bytes(payload)
    command = [sys.executable, str(ROOT / "tests/ipc_fuzz_probe.py")]
    if args.replay_only:
        command.extend(["--replay-only", *map(str, sorted(corpus.iterdir()))])
    else:
        command.extend([str(corpus), f"-runs={args.runs}", f"-max_total_time={args.seconds}",
                        f"-seed={args.seed}", f"-max_len={MAX_INPUT_BYTES}", "-timeout=5",
                        "-rss_limit_mb=512", f"-artifact_prefix={output}/", "-print_final_stats=1"])
    (output / "run.json").write_text(json.dumps({
        "target": "gambit.ipc_validation", "native_decoder": False,
        "source_commit": os.environ.get("GITHUB_SHA"), "command": command,
        "seed": args.seed, "runs": args.runs, "seconds": args.seconds,
        "replay_only": args.replay_only,
    }, indent=2) + "\n")
    try:
        with (output / "fuzz.log").open("w") as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                    timeout=args.seconds + 30)
    finally:
        print((output / "fuzz.log").read_text(errors="replace"))
    result.check_returncode()
    if not args.replay_only:
        # Reject empty/no-op campaigns even if a worker exits successfully.
        report = (output / "fuzz.log").read_text(errors="replace")
        if not re.search(r"cov: [1-9][0-9]*", report) or "Done " not in report:
            raise RuntimeError("IPC campaign did not report coverage and normal completion")
    print(f"Passed IPC {'seed replay (NOT coverage-guided)' if args.replay_only else 'coverage-guided Python preflight campaign'}")


if __name__ == "__main__":
    main()
