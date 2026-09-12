"""Build a Linux pre-interpreter LSan launcher and verify probe + leak control."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
import sysconfig
from pathlib import Path


def build_launcher(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=False)
    library_dir = sysconfig.get_config_var("LIBDIR")
    library_name = sysconfig.get_config_var("LDLIBRARY")
    if not library_dir or not library_name or not library_name.endswith(".so"):
        raise RuntimeError("Linux leak probe requires a shared CPython development library")
    launcher = directory / "native-lsan-python"
    environment = dict(os.environ)
    environment.pop("LD_PRELOAD", None)
    subprocess.run([
        *shlex.split(os.environ.get("CC", "cc")), "-std=c11", "-O1", "-g",
        "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
        "-fno-omit-frame-pointer", "-Wl,--export-dynamic",
        "-isystem", sysconfig.get_path("include"),
        str(Path(__file__).with_name("native_lsan_python.c")),
        str(Path(library_dir) / library_name), f"-Wl,-rpath,{library_dir}",
        *shlex.split(sysconfig.get_config_var("LIBS") or ""),
        *shlex.split(sysconfig.get_config_var("SYSLIBS") or ""),
        "-o", str(launcher),
    ], env=environment, check=True, timeout=60)
    return launcher


def verify_result(result: subprocess.CompletedProcess[str], *, control: bool) -> None:
    output = result.stdout + result.stderr
    if control:
        # A crash, missing runtime, or unrelated leak is not a positive control.
        if (result.returncode != 1 or "NumPy deliberate leak control exercised" not in output
                or "fault_malloc" not in output
                or not re.search(r"SUMMARY: AddressSanitizer: 16 byte\(s\) leaked in 1 allocation\(s\)", output)):
            raise RuntimeError("LSan did not identify the deliberate NumPy data-buffer leak")
    elif result.returncode != 0 or "425 injected native failures" not in output or "LeakSanitizer result=0" not in output:
        raise RuntimeError("NumPy allocation-failure leak check failed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("this qualification runner requires Linux LeakSanitizer")
    if os.environ.get("GAMBIT_SANITIZER_RUN") != "1":
        parser.error("GAMBIT_SANITIZER_RUN=1 is required (use a sanitized Gambit build)")
    root = args.build_dir.resolve()
    launcher = build_launcher(root)
    environment = dict(os.environ)
    # The parent/compiler run normally; only the embedded probe needs preloads.
    environment["LD_PRELOAD"] = ":".join(os.environ[name] for name in ("ASAN_LIBRARY", "CXX_LIBRARY"))
    environment["ASAN_OPTIONS"] = "detect_leaks=1:halt_on_error=1"
    environment["LSAN_OPTIONS"] = "print_suppressions=1"
    environment["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
    for control in (False, True):
        result = subprocess.run([
            str(launcher), sys.executable,
            str(Path(__file__).with_name("native_numpy_allocator_probe.py")),
            "--build-dir", str(root / ("control" if control else "probe")),
            "--leak-check", *(["--positive-control"] if control else []),
        ], env=environment, capture_output=True, text=True, timeout=120)
        (root / ("control.log" if control else "probe.log")).write_text(result.stdout + result.stderr)
        print(result.stdout, end="", flush=True)
        print(result.stderr, end="", file=sys.stderr, flush=True)
        verify_result(result, control=control)
    print("Unsuppressed NumPy leak check passed; deliberate 16-byte leak detected")


if __name__ == "__main__":
    main()
