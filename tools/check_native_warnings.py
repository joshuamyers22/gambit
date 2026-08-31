"""Compile every project-owned C++ translation unit with warnings as errors."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sysconfig
import tempfile
from pathlib import Path

import numpy as np
import pybind11

ROOT = Path(__file__).resolve().parents[1]
CPP_ROOT = ROOT / "src" / "gambit" / "cpp"
SOURCES = (
    CPP_ROOT / "factor_cache" / "mapped_column.cpp",
    CPP_ROOT / "factor_cache" / "tick_ring.cpp",
    CPP_ROOT / "io" / "csv_reader.cpp",
    CPP_ROOT / "io" / "main.cpp",
    CPP_ROOT / "io" / "read_file.cpp",
    CPP_ROOT / "options" / "options.cpp",
    CPP_ROOT / "options" / "pybind.cpp",
    CPP_ROOT / "options" / "pybind_options.cpp",
)


def _libzip_include_dirs() -> list[str]:
    include_dirs: list[str] = []
    if shutil.which("pkg-config"):
        result = subprocess.run(
            ["pkg-config", "--cflags-only-I", "libzip"],
            check=True,
            capture_output=True,
            text=True,
        )
        include_dirs.extend(token[2:] for token in shlex.split(result.stdout) if token.startswith("-I"))
    for prefix in (os.environ.get("LIBZIP_PREFIX"), "/opt/homebrew/opt/libzip", "/usr/local/opt/libzip"):
        if prefix and (include := Path(prefix) / "include").is_dir():
            include_dirs.append(str(include))
    return include_dirs


def main() -> None:
    compiler = os.environ.get("CXX", "c++")
    system_includes = {
        sysconfig.get_path("include"),
        np.get_include(),
        pybind11.get_include(),
        *_libzip_include_dirs(),
    }
    with tempfile.TemporaryDirectory(prefix="gambit-native-warnings-") as directory:
        output = Path(directory)
        for source in SOURCES:
            command = [
                compiler,
                "-std=c++11",
                "-Wall",
                "-Wextra",
                "-Wpedantic",
                "-Werror",
                *[argument for include in sorted(system_includes) for argument in ("-isystem", include)],
                "-c",
                str(source),
                "-o",
                str(output / f"{source.parent.name}-{source.stem}.o"),
            ]
            subprocess.run(command, check=True, cwd=ROOT)


if __name__ == "__main__":
    main()
