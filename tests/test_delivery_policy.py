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
    for name in ("test", "integration", "native", "notebooks", "native-fuzz", "native-sanitizers", "native-thread-sanitizer", "dependency-audit", "package"):
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


def test_native_fuzz_gate_covers_both_formats_and_retains_failures():
    job = workflow("ci.yml")["jobs"]["native-fuzz"]
    assert job["strategy"]["matrix"]["format"] == ["csv", "zip"]
    assert int(job["timeout-minutes"]) <= 10
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert "tools/run_native_fuzz.py" in commands
    assert "--runs 10000 --seconds 30" in commands
    assert "--replay-only" not in commands
    artifact = next(step for step in job["steps"] if "upload-artifact@" in step.get("uses", ""))
    assert artifact["if"] == "failure()"
    assert artifact["with"]["retention-days"] == "7"


def test_numpy_allocator_probe_requires_instrumentation_and_leak_checking():
    job = workflow("ci.yml")["jobs"]["native-sanitizers"]
    step = next(step for step in job["steps"]
                if "tests/run_numpy_leak_check.py" in step.get("run", ""))
    # A failure in the preceding stress probe must not hide this independent
    # evidence; a failed build already fails the job and cannot run the probe.
    assert step["if"] == "${{ !cancelled() && steps.native-build.outcome == 'success' }}"
    build = next(item for item in job["steps"] if item.get("id") == "native-build")
    assert build["env"]["GAMBIT_SANITIZE"] == "1"
    assert step.get("continue-on-error", "false") == "false"
    assert "--build-dir" in step["run"]
    assert "LD_PRELOAD" not in step["run"], "only the scoped child should preload sanitizers"
    assert step["env"]["GAMBIT_SANITIZER_RUN"] == "1"
    assert "detect_leaks=1" in step["env"]["ASAN_OPTIONS"]
    assert not any(option.startswith("suppressions=")
                   for option in step["env"].get("LSAN_OPTIONS", "").split(":"))
    evidence = next(item for item in job["steps"] if "upload-artifact@" in item.get("uses", ""))
    assert evidence["if"] == "always()"
    assert evidence["with"]["path"] == "${{ runner.temp }}/gambit-numpy-allocator/*.log"
    assert evidence["with"]["retention-days"] == "7"


def test_native_stress_probe_requires_a_real_leak_runtime():
    job = workflow("ci.yml")["jobs"]["native-sanitizers"]
    step = next(item for item in job["steps"]
                if "python tests/native_memory_probe.py" in item.get("run", ""))
    assert "--require-lsan" in step["run"]
    assert "detect_leaks=1" in step["env"]["ASAN_OPTIONS"]


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
