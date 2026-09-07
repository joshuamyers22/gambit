"""Protect the quality gates that must precede package publication."""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]


def workflow(name):
    # BaseLoader preserves GitHub's YAML 1.2 `on` key rather than treating it as True.
    return yaml.load((ROOT / ".github" / "workflows" / name).read_text(), Loader=yaml.BaseLoader)


def ancestors(jobs, name):
    dependencies = jobs[name].get("needs", [])
    if isinstance(dependencies, str):
        dependencies = [dependencies]
    return set(dependencies).union(*(ancestors(jobs, dependency) for dependency in dependencies))


@pytest.mark.parametrize("publisher", ["publish-pypi", "publish-testpypi"])
def test_publication_requires_same_commit_quality_and_artifact_verification(publisher):
    jobs = workflow("release.yml")["jobs"]
    required = ancestors(jobs, publisher)
    assert {"quality", "verify", "wheels", "sdist"} <= required
    assert jobs["quality"]["uses"] == "./.github/workflows/ci.yml"
    for gate in ("quality", "verify", "sdist", "wheels"):
        assert "if" not in jobs[gate], f"{gate} must not skip quality verification"
        assert jobs[gate].get("continue-on-error", "false") == "false"
    assert "workflow_call" in workflow("ci.yml")["on"]


def test_required_ci_retains_sanitizers_audit_and_benchmark_correctness():
    jobs = workflow("ci.yml")["jobs"]
    for name in ("test", "integration", "native", "notebooks", "native-sanitizers", "native-thread-sanitizer", "dependency-audit", "package"):
        assert "lock" in ancestors(jobs, name)
        assert "if" not in jobs[name], f"required quality job {name} must not be conditional"
        assert jobs[name].get("continue-on-error", "false") == "false"
    commands = "\n".join(step.get("run", "") for step in jobs["integration"]["steps"])
    assert 'pytest -m "integration or performance"' in commands
    sanitizer_commands = "\n".join(step.get("run", "") for step in jobs["native-sanitizers"]["steps"])
    assert "uv sync --frozen" in sanitizer_commands
    assert "--no-cache" in sanitizer_commands, "sanitizers must not reuse an unsanitized extension build"
    package_commands = "\n".join(step.get("run", "") for step in jobs["package"]["steps"])
    assert "uv build --python python" in package_commands, "wheel ABI must match the configured package-job interpreter"


@pytest.mark.parametrize("name", ["ci.yml", "docs.yml", "performance.yml"])
def test_reference_workflows_use_frozen_installs_and_read_only_credentials(name):
    config = workflow(name)
    assert config["permissions"] == {"contents": "read"}
    for job in config["jobs"].values():
        for step in job.get("steps", []):
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step.get("with", {}).get("persist-credentials") == "false"
            command = step.get("run", "")
            assert "pip install -e" not in command
            if "uv sync" in command:
                assert "uv sync --frozen" in command
