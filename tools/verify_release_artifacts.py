#!/usr/bin/env python3
"""Fail closed when Gambit release archives are incomplete or contaminated."""

from __future__ import annotations

import argparse
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import parse_wheel_filename

ROOT = Path(__file__).resolve().parents[1]
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())['project']
NATIVE_MODULES = ("_factor_cache", "_io", "_options", "compute_pnl")
NATIVE_BUILD_SOURCES = {
    "src/gambit/compute_pnl.pyx",
    "src/gambit/cpp/factor_cache/mapped_column.cpp",
    "src/gambit/cpp/io/csv_reader.cpp",
    "src/gambit/cpp/options/pybind_options.cpp",
}
WHEEL_SOURCE_SUFFIXES = (".c", ".cc", ".cpp", ".h", ".hpp", ".pyx")
FORBIDDEN_PARTS = {"__pycache__", "documentation", "examples", "tests"}


def fail(message: str) -> None:
    raise SystemExit(f"release artifact verification failed: {message}")


def contaminated(names: list[str]) -> list[str]:
    return [
        name
        for name in names
        if FORBIDDEN_PARTS.intersection(PurePosixPath(name).parts)
        or name.endswith((".pyc", ".pyo", ".DS_Store"))
    ]


def verify_wheel(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        bad = contaminated(names)
        bad.extend(name for name in names if name.startswith("gambit/") and name.endswith(WHEEL_SOURCE_SUFFIXES))
        if bad:
            fail(f"{path.name} contains development/source artifacts: {sorted(set(bad))}")
        if "gambit/__init__.py" not in names:
            fail(f"{path.name} does not contain gambit/__init__.py")
        for module in NATIVE_MODULES:
            if not any(PurePosixPath(name).name.startswith(f"{module}.") and name.startswith("gambit/") for name in names):
                fail(f"{path.name} does not contain native module gambit.{module}")

        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            fail(f"{path.name} has {len(metadata_names)} METADATA files")
        metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
        if metadata["Name"] != PROJECT["name"] or metadata["Version"] != PROJECT["version"]:
            fail(f"{path.name} name/version metadata differs from pyproject.toml")
        if SpecifierSet(metadata["Requires-Python"]) != SpecifierSet(PROJECT["requires-python"]):
            fail(f"{path.name} Requires-Python differs from pyproject.toml")

        actual_core = {
            Requirement(value).name.lower()
            for value in metadata.get_all("Requires-Dist", [])
            if Requirement(value).marker is None
        }
        expected_core = {Requirement(value).name.lower() for value in PROJECT["dependencies"]}
        if actual_core != expected_core:
            fail(f"{path.name} core dependencies differ: {actual_core} != {expected_core}")
        actual_extras = set(metadata.get_all("Provides-Extra", []))
        expected_extras = set(PROJECT["optional-dependencies"])
        if actual_extras != expected_extras:
            fail(f"{path.name} extras differ: {actual_extras} != {expected_extras}")
        metadata_requirements = [Requirement(value) for value in metadata.get_all("Requires-Dist", [])]
        for extra, declared in PROJECT["optional-dependencies"].items():
            actual_optional = {
                (requirement.name.lower(), str(requirement.specifier))
                for requirement in metadata_requirements
                if requirement.marker is not None and requirement.marker.evaluate({"extra": extra})
            }
            expected_optional = {
                (requirement.name.lower(), str(requirement.specifier))
                for requirement in map(Requirement, declared)
                if requirement.marker is None or requirement.marker.evaluate()
            }
            if actual_optional != expected_optional:
                fail(f"{path.name} [{extra}] dependencies differ: {actual_optional} != {expected_optional}")

        license_names = {PurePosixPath(name).name for name in names if ".dist-info/licenses/" in name}
        required_licenses = {"LICENSE.txt", "THIRD_PARTY_NOTICES.md"}
        if not required_licenses.issubset(license_names):
            fail(f"{path.name} is missing license files: {required_licenses - license_names}")


def verify_sdist(path: Path) -> None:
    with tarfile.open(path, "r:gz") as archive:
        names = archive.getnames()
    bad = contaminated(names)
    if bad:
        fail(f"{path.name} contains development artifacts: {bad}")
    relative = {str(PurePosixPath(name).relative_to(PurePosixPath(name).parts[0])) for name in names if "/" in name}
    required = {"LICENSE.txt", "THIRD_PARTY_NOTICES.md", "README.rst", "pyproject.toml", "setup.py", "MANIFEST.in"}
    missing = required - relative
    if missing:
        fail(f"{path.name} is missing project files: {missing}")
    missing_sources = NATIVE_BUILD_SOURCES - relative
    if missing_sources:
        fail(f"{path.name} lacks native build sources: {missing_sources}")


def verify_matrix(wheels: list[Path]) -> None:
    expected = {(python, platform) for python in ("cp310", "cp311", "cp312") for platform in ("linux-x86_64", "macos-x86_64", "macos-arm64")}
    actual: set[tuple[str, str]] = set()
    for wheel in wheels:
        _, _, _, tags = parse_wheel_filename(wheel.name)
        for tag in tags:
            platform = str(tag.platform)
            family = None
            if "linux" in platform and platform.endswith("x86_64"):
                family = "linux-x86_64"
            elif platform.startswith("macosx") and platform.endswith("x86_64"):
                family = "macos-x86_64"
            elif platform.startswith("macosx") and platform.endswith("arm64"):
                family = "macos-arm64"
            if family:
                actual.add((str(tag.interpreter), family))
    if actual != expected:
        fail(f"wheel matrix differs; missing={sorted(expected - actual)}, unexpected={sorted(actual - expected)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--expected-matrix", action="store_true")
    args = parser.parse_args()
    wheels = sorted(args.directory.glob("*.whl"))
    sdists = sorted(args.directory.glob("*.tar.gz"))
    if not wheels or len(sdists) != 1:
        fail(f"expected wheels and exactly one sdist, found {len(wheels)} wheel(s) and {len(sdists)} sdist(s)")
    version_file = (ROOT / "version.txt").read_text().strip()
    if version_file != PROJECT["version"]:
        fail(f"version.txt ({version_file}) differs from pyproject.toml ({PROJECT['version']})")
    for wheel in wheels:
        verify_wheel(wheel)
    verify_sdist(sdists[0])
    if args.expected_matrix:
        verify_matrix(wheels)
    print(f"verified {len(wheels)} wheel(s) and {sdists[0].name}")


if __name__ == "__main__":
    main()
