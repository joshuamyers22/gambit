#!/usr/bin/env python3
"""Install release artifacts into disposable environments and smoke-test them."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from packaging.tags import sys_tags
from packaging.utils import parse_wheel_filename

EXTRA_IMPORTS = {
    "calendars": "import pandas_market_calendars; import gambit.holiday_calendars",
    "persistence": "import h5py; import gambit.pq_io",
    "research": "import scipy; import statsmodels; import gambit.risk",
    "visualization": "import IPython; import ipywidgets; import plotly; import gambit.interactive_plot",
    "notebooks": "import h5py, IPython, ipykernel, ipywidgets, nbclient, nbconvert, nbformat, pandas_market_calendars, plotly, scipy, statsmodels, traitlets",
}
CORE_SMOKE = "import gambit, gambit._factor_cache, gambit._io, gambit._options, gambit.compute_pnl; from importlib.metadata import version; assert version('gambit-markets') == gambit.__version__"


def compatible_wheel(directory: Path) -> Path:
    supported = set(sys_tags())
    matches = []
    for path in directory.glob("*.whl"):
        _, _, _, tags = parse_wheel_filename(path.name)
        if supported.intersection(tags):
            matches.append(path.resolve())
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one compatible wheel, found: {matches}")
    return matches[0]


def verify_install(specification: str, smoke: str, label: str) -> None:
    temporary_parent = "/tmp" if Path("/tmp").is_dir() else None
    with tempfile.TemporaryDirectory(prefix=f"gambit-{label}-", dir=temporary_parent) as temporary:
        root = Path(temporary)
        root.chmod(0o755)
        environment = root
        subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True)
        python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        command = environment / ("Scripts/gambit-factor-cache.exe" if sys.platform == "win32" else "bin/gambit-factor-cache")
        outside_checkout = root.parent
        subprocess.run([str(python), "-m", "pip", "install", specification], cwd=outside_checkout, check=True)
        subprocess.run([str(python), "-c", f"{CORE_SMOKE}; {smoke}"], cwd=outside_checkout, check=True)
        subprocess.run([str(command), "--help"], cwd=outside_checkout, check=True, stdout=subprocess.DEVNULL)
        print(f"verified isolated {label} installation")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    wheel = compatible_wheel(args.directory)
    verify_install(str(wheel), "", "core-wheel")
    for extra, imports in EXTRA_IMPORTS.items():
        verify_install(f"{wheel}[{extra}]", imports, f"{extra}-wheel")
    sdists = list(args.directory.glob("*.tar.gz"))
    if len(sdists) != 1:
        raise SystemExit(f"expected exactly one sdist, found: {sdists}")
    verify_install(str(sdists[0].resolve()), "", "core-sdist")


if __name__ == "__main__":
    main()
