"""Build and run bounded CSV/ZIP libFuzzer campaigns, preserving artifacts.

Requires Clang with libFuzzer/ASan/UBSan and libzip development files. No Python
extension is imported. The output directory must not already exist.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEEDS = (
    b"",
    b"0\n1\n-1\n",
    b"2147483647\n-2147483648\n",
    b"2147483648\n",
    b"-2147483649\n",
    b"9223372036854775807\n-9223372036854775808\n",
    b"9223372036854775808\n",
    b"-9223372036854775809\n",
    b"9" * 1024 + b"\n",
    b"abc,1.5\r\ndef,-2.5",
    b"\x00,\xff\n",
    b"true\nfalse\n1e300\n-1e-300\n",
)


def libzip_flags() -> list[str]:
    if shutil.which("pkg-config"):
        return shlex.split(subprocess.check_output(
            ["pkg-config", "--cflags", "--libs", "libzip"], text=True, timeout=15,
        ))
    flags = ["-lzip"]
    for prefix in (os.environ.get("LIBZIP_PREFIX"), "/opt/homebrew/opt/libzip", "/usr/local/opt/libzip"):
        if prefix and (Path(prefix) / "include").is_dir():
            flags.extend(["-I", str(Path(prefix) / "include"), "-L", str(Path(prefix) / "lib")])
    return flags


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--format", choices=("csv", "zip"), default="csv")
    parser.add_argument("--runs", type=int, default=10000)
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20260911)
    parser.add_argument("--replay-only", action="store_true",
                        help="ASan/UBSan seed replay only; no coverage-guided fuzzing")
    args = parser.parse_args()
    if args.runs <= 0 or args.seconds <= 0 or not 0 < args.seed < 2**32:
        parser.error("runs/seconds must be positive; seed must fit a nonzero uint32")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    corpus = output / "corpus"
    corpus.mkdir()
    for index, payload in enumerate(SEEDS):
        if args.format == "zip":
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr(zipfile.ZipInfo("data.csv", (2026, 1, 1, 0, 0, 0)), payload,
                                 compress_type=zipfile.ZIP_DEFLATED)
            payload = buffer.getvalue()
        (corpus / f"seed-{index:02d}").write_bytes(payload)
    executable = output / "native-csv-fuzz"
    command = [
        *shlex.split(os.environ.get("FUZZ_CXX", "clang++")),
        "-std=c++11", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
        "-fsanitize=address,undefined" if args.replay_only else "-fsanitize=fuzzer,address,undefined",
        "-fno-sanitize-recover=all", "-fno-omit-frame-pointer",
        "-I", str(ROOT / "src/gambit/cpp/io"),
        str(ROOT / "tests/native_csv_fuzz.cpp"), str(ROOT / "src/gambit/cpp/io/csv_reader.cpp"),
        *([str(ROOT / "tests/native_csv_fuzz_replay.cpp")] if args.replay_only else []),
        *libzip_flags(), "-o", str(executable),
    ]
    compiler_environment = dict(os.environ)
    compiler_environment.pop("LD_PRELOAD", None)
    subprocess.run(command, check=True, timeout=60, env=compiler_environment)
    environment = dict(
        os.environ,
        GAMBIT_FUZZ_INPUT=str(output / "input"), GAMBIT_FUZZ_FORMAT=args.format,
        ASAN_OPTIONS=f"detect_leaks={int(sys.platform != 'darwin')}:halt_on_error=1",
        UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1",
    )
    # Do not mix a Python/GCC sanitizer preload with this Clang-owned runtime.
    environment.pop("LD_PRELOAD", None)
    run_command = ([str(executable), *map(str, sorted(corpus.iterdir()))] if args.replay_only else [
        str(executable), str(corpus), f"-runs={args.runs}", f"-seed={args.seed}",
        f"-max_total_time={args.seconds}", "-max_len=65536", "-timeout=5",
        "-rss_limit_mb=512", f"-artifact_prefix={output}/", "-print_final_stats=1",
    ])
    (output / "run.json").write_text(json.dumps({
        "format": args.format, "seed": args.seed, "runs": args.runs,
        "seconds": args.seconds, "replay_only": args.replay_only,
        "source_commit": os.environ.get("GITHUB_SHA"), "platform": sys.platform,
        "compiler_command": command, "run_command": run_command,
        "asan_options": environment["ASAN_OPTIONS"],
        "ubsan_options": environment["UBSAN_OPTIONS"],
    }, indent=2) + "\n")
    try:
        with (output / "fuzz.log").open("w") as log:
            result = subprocess.run(run_command, env=environment, stdout=log,
                                    stderr=subprocess.STDOUT, timeout=args.seconds + 15)
    finally:
        # A parent timeout must leave diagnostics visible as well as on disk.
        print((output / "fuzz.log").read_text(errors="replace"))
    result.check_returncode()
    print(f"Passed {args.format} {'sanitizer seed replay (NOT coverage-guided)' if args.replay_only else 'libFuzzer campaign'}")


if __name__ == "__main__":
    main()
