"""Build an isolated factor extension using setup.py's C++11 compiler settings.

Requires a built source tree and the project's build dependencies. Never changes
the source tree or its installed extension. Rigtorp is an explicit local,
hash-checked benchmark input; this command performs no network access.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pybind11
from setuptools import Distribution, Extension
from setuptools.command.build_ext import build_ext

PIN = "1053918dbd251fbff69b24ef27fa5d51c29ec2af"
HEADER_SHA256 = "1e631ec9e8ba4955da5cac116620815055f7da0ea936bfdb3036c4e87bc8a6e8"


def build(source, output, rigtorp=None, sanitize=False):
    source, output = source.resolve(), output.resolve()
    if output == source or source in output.parents:
        raise ValueError("build output must be outside the source tree")
    if rigtorp:
        header = rigtorp / "include/rigtorp/SPSCQueue.h"
        if hashlib.sha256(header.read_bytes()).hexdigest() != HEADER_SHA256:
            raise ValueError("Rigtorp header does not match the recorded pin")
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(source / "src", output / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
    cpp = output / "src/gambit/cpp/factor_cache"
    if rigtorp:
        shutil.copy2(Path(__file__).with_name("rigtorp_ring_adapter.hpp"), cpp / "spsc_ring.hpp")
        shutil.copy2(header, cpp / "rigtorp_SPSCQueue.h")
        shutil.copy2(rigtorp / "LICENSE", output / "RIGTORP_LICENSE")
    sanitizer = ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"] if sanitize else []
    extension = Extension("gambit._factor_cache",
                          sources=[str(cpp / name) for name in ("mapped_column.cpp", "tick_ring.cpp", "top_of_book_backtest.cpp")],
                          include_dirs=[pybind11.get_include(), np.get_include()], language="c++",
                          extra_compile_args=["-std=c++11", "-O1" if sanitize else "-O3", *sanitizer],
                          extra_link_args=sanitizer)
    commands = []

    class RecordingBuild(build_ext):
        def build_extensions(self):
            method = "call" if hasattr(self.compiler, "call") else "spawn"
            original = getattr(self.compiler, method)

            def spawn(command, **kwargs):
                commands.append(list(command))
                return original(command, **kwargs)

            setattr(self.compiler, method, spawn)
            super().build_extensions()

    distribution = Distribution(dict(name="gambit-handoff-benchmark", ext_modules=[extension],
                                     cmdclass={"build_ext": RecordingBuild}))
    command = distribution.get_command_obj("build_ext")
    command.build_lib = str(output / "built")
    command.build_temp = str(output / "objects")
    command.force = True
    distribution.run_command("build_ext")
    native = Path(command.get_ext_fullpath(extension.name))
    destination = output / "src/gambit" / native.name
    # New inode also avoids macOS's cached signature for an overwritten Mach-O.
    shutil.copy2(native, str(destination) + ".new")
    os.replace(str(destination) + ".new", destination)
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = dict(python=sys.version, platform=platform.platform(), source=str(source),
                    compiler=subprocess.check_output([os.environ.get("CXX", "c++"), "--version"], text=True),
                    compile_args=extension.extra_compile_args, commands=commands, sanitized=sanitize,
                    rigtorp_commit=PIN if rigtorp else None, native_sha256=digest(destination),
                    sources={str(path.relative_to(output)): digest(path) for path in cpp.iterdir() if path.is_file()})
    (output / "build.json").write_text(json.dumps(manifest, indent=2) + "\n")
    wrapper = output / "python"
    wrapper.write_text("#!/bin/sh\nexport PYTHONPATH=" + shlex.quote(str(output / "src")) +
                       "\nexec " + shlex.quote(sys.executable) + ' "$@"\n')
    wrapper.chmod(0o755)
    print(wrapper)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rigtorp", type=Path)
    parser.add_argument("--sanitize", action="store_true")
    args = parser.parse_args()
    build(args.source, args.output, args.rigtorp, args.sanitize)


if __name__ == "__main__":
    main()
