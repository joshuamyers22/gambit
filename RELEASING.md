# Releasing Gambit Markets

Releases are built by GitHub Actions and published through PyPI Trusted
Publishing. Do not upload artifacts built on a workstation.

## One-time repository configuration

1. Create GitHub environments named `testpypi`, `pypi`, and `github-pages`.
2. Require a maintainer approval for the `pypi` environment.
3. On PyPI, create a pending trusted publisher with:
   - PyPI project: `gambit-markets`
   - GitHub owner: `joshuamyers22`
   - Repository: `gambit`
   - Workflow: `release.yml`
   - Environment: `pypi`
4. Repeat on TestPyPI with environment `testpypi`.
5. Configure GitHub Pages to deploy through GitHub Actions.
6. Protect `main`; require CI and documentation checks before merge.

TestPyPI and PyPI are separate services and require separate accounts and
trusted-publisher records.

## Prepare a release

1. Pull `main` and require a clean working tree.
2. Set the same PEP 440 version in `pyproject.toml` and `version.txt`.
3. Move entries from `Unreleased` into a dated changelog section.
4. Run:

   ```bash
   python -m pytest
   python -m ruff check src tests
   python -m mypy
   python -m sphinx -W --keep-going -b html documentation/source documentation/generated
   python -m build
   python -m twine check dist/*
   python -m pip_audit --strict .
   ```

5. Push the release commit and wait for every required GitHub check.
6. Run `Release distributions` manually. This publishes only to TestPyPI.
7. In clean Linux and macOS environments, install the appropriate TestPyPI
   wheel without falling back to the source distribution and run an import/CLI
   smoke test.

## Publish

Create a signed tag named `vX.Y.Z`, then create and publish a GitHub release from
that exact tag. Publishing the GitHub release triggers production PyPI. The
workflow rebuilds all artifacts from the tagged source, repairs native library
dependencies, verifies metadata, and publishes with short-lived OIDC credentials.

PyPI files are immutable. If a release is wrong, increment the version and
publish a corrective release; never attempt to replace an uploaded file.

## Post-release verification

1. Confirm the PyPI page shows the sdist and all expected CPython/platform wheels.
2. Confirm the files show Trusted Publishing and attestations.
3. Install from PyPI in clean Linux and macOS environments.
4. Verify `gambit.__version__`, native imports, and `gambit-factor-cache --help`.
5. Confirm the hosted documentation reports the released version.
6. Reset `CHANGELOG.md` to an empty `Unreleased` section for the next cycle.
