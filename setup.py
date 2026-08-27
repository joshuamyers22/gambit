"""Native extension definitions for the gambit package."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pybind11
from Cython.Build import cythonize
from setuptools import Extension, setup

CPP_DIR = Path("src") / "gambit" / "cpp"


def _libzip_paths() -> tuple[list[str], list[str]]:
    """Return optional libzip search paths from standard environment hints."""
    prefixes = [
        os.environ.get("LIBZIP_PREFIX"),
        os.environ.get("CONDA_PREFIX"),
        "/opt/homebrew/opt/libzip",
        "/usr/local/opt/libzip",
    ]
    include_dirs: list[str] = []
    library_dirs: list[str] = []
    for value in prefixes:
        if not value:
            continue
        prefix = Path(value)
        include = prefix / "include"
        library = prefix / "lib"
        if include.is_dir():
            include_dirs.append(str(include))
        if library.is_dir():
            library_dirs.append(str(library))
    return include_dirs, library_dirs


def _extensions() -> list[Extension]:
    is_windows = sys.platform == "win32"
    cpp_args = [] if is_windows else ["-std=c++11", "-O3"]
    cython_args = [] if is_windows else [
        "-Wno-parentheses-equality",
        "-Wno-unreachable-code-fallthrough",
        "-O3",
    ]
    zip_includes, zip_libraries = _libzip_paths()

    io_extension = Extension(
        "gambit._io",
        sources=[
            str(CPP_DIR / "io" / "read_file.cpp"),
            str(CPP_DIR / "io" / "csv_reader.cpp"),
        ],
        include_dirs=zip_includes + [np.get_include()],
        library_dirs=zip_libraries,
        libraries=["zip"],
        language="c++",
        extra_compile_args=cpp_args,
    )

    option_sources = sorted((CPP_DIR / "options").glob("*.cpp"))
    option_sources += sorted((CPP_DIR / "lets_be_rational").glob("*.cpp"))
    options_extension = Extension(
        "gambit._options",
        sources=[str(path) for path in option_sources],
        include_dirs=[pybind11.get_include()],
        language="c++",
        extra_compile_args=cpp_args,
    )

    pnl_extension = Extension(
        "gambit.compute_pnl",
        [str(Path("src") / "gambit" / "compute_pnl.pyx")],
        include_dirs=[np.get_include()],
        extra_compile_args=cython_args,
        define_macros=[("NPY_NO_DEPRECATED_API", "NPY_1_7_API_VERSION")],
    )
    cython_pnl = cythonize(
        [pnl_extension],
        compiler_directives={"language_level": "3"},
    )[0]
    return [io_extension, options_extension, cython_pnl]


setup(ext_modules=_extensions())
