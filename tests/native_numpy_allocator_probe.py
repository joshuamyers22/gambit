"""Isolated NumPy data-allocation fault injection; no production test hooks.

Builds a tiny test extension against the active interpreter/NumPy. Run this file
in a disposable subprocess. --leak-check requires an injected Linux LSan runtime.
"""

from __future__ import annotations

import argparse
import ctypes
import gc
import importlib
import os
import shlex
import subprocess
import sys
import sysconfig
import zipfile
from pathlib import Path


def build(directory: Path) -> None:
    import numpy as np

    directory.mkdir(parents=True, exist_ok=False)
    extension = directory / ("_gambit_numpy_allocator" + sysconfig.get_config_var("EXT_SUFFIX"))
    environment = dict(os.environ)
    environment.pop("LD_PRELOAD", None)
    sanitizer_flags = (["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
                       if os.environ.get("GAMBIT_SANITIZER_RUN") == "1" else [])
    subprocess.run([
        *shlex.split(os.environ.get("CC", "cc")), "-std=c11", "-O1", "-g",
        "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", *sanitizer_flags,
        *(["-undefined", "dynamic_lookup"] if sys.platform == "darwin" else []),
        "-isystem", sysconfig.get_path("include"), "-isystem", np.get_include(),
        str(Path(__file__).with_name("native_numpy_allocator.c")), "-o", str(extension),
    ], env=environment, check=True, timeout=60)


def exercise(directory: Path, enable_tracking, disable_tracking) -> None:
    import numpy as np

    from gambit import _io

    sys.path.insert(0, str(directory))
    probe = importlib.import_module("_gambit_numpy_allocator")
    original = probe.current_policy()
    assert probe.policy_count() == 0

    def invoke(policy, function, *args, **kwargs):
        enable_tracking()
        try:
            return probe.invoke(policy, function, args, kwargs)
        finally:
            disable_tracking()
            assert probe.current_policy() is original

    def failure(function, args, kwargs, index):
        policy = probe.new_policy(index)
        try:
            invoke(policy, function, *args, **kwargs)
        except MemoryError:
            pass
        else:
            raise AssertionError(f"allocation {index} did not fail")
        assert probe.stats(policy) == (index + 1, index, index, 1)
        del policy
        assert probe.policy_count() == 0

    # Negative controls: prove that both malloc and calloc routes can fail and
    # that live arrays remain counted until they are actually released.
    for function in (np.empty, np.zeros):
        failure(function, ((8,),), {}, 0)
        policy = probe.new_policy(-1)
        array = invoke(policy, function, (8,))
        assert probe.stats(policy) == (1, 1, 0, 0)
        del array
        assert probe.stats(policy) == (1, 1, 1, 0)
        del policy
    assert probe.policy_count() == 0

    read_args = ([0, 1, 2, 3], ["S8", "i8", "f8", "M8[ns]"], ",", 0, 0)
    for empty in (False, True):
        payload = "" if empty else "alpha,1,1.5,42\nbeta,-2,-2.5,43\n"
        source = directory / f"values-{empty}.csv"
        source.write_text(payload)
        archive = directory / f"values-{empty}.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            output.writestr("data.csv", payload)
        for filename in (str(source), f"{archive}:data.csv"):
            # Repetition catches retained data/policies after partial list creation.
            for _ in range(25):
                for index in range(4):
                    failure(_io.read_file, (filename, *read_args), {}, index)
                policy = probe.new_policy(4)  # all four allocations succeed
                arrays = invoke(policy, _io.read_file, filename, *read_args)
                assert probe.stats(policy) == (4, 4, 0, 0)
                assert len(arrays) == 4
                assert all(array.flags.owndata and array.base is None for array in arrays)
                expected = [[], [], [], []] if empty else [[b"alpha", b"beta"], [1, -2], [1.5, -2.5], [42, 43]]
                assert [array.tolist() for array in arrays[:3]] == expected[:3]
                assert arrays[3].view("i8").tolist() == expected[3]
                del arrays
                assert probe.stats(policy) == (4, 4, 4, 0)
                del policy
                assert probe.policy_count() == 0

    # Restoring the default handler and dropping the caller's policy reference
    # must not invalidate arrays: their views retain the original handler.
    policy = probe.new_policy(-1)
    arrays = invoke(policy, _io.read_file, str(directory / "values-False.csv"), *read_args)
    views = [array[::2] for array in arrays]
    del arrays, policy
    assert probe.policy_count() == 1
    assert views[0].tolist() == [b"alpha"] and views[1].tolist() == [1]
    del views
    assert probe.policy_count() == 0

    datetimes = np.array(["2026-09-11T12:00:00", "2026-09-12T00:00:00"])
    for _ in range(25):
        failure(_io.parse_datetimes, (datetimes,), {}, 0)
        policy = probe.new_policy(1)
        result = invoke(policy, _io.parse_datetimes, datetimes)
        assert probe.stats(policy) == (1, 1, 0, 0)
        assert result.flags.owndata
        assert np.array_equal(result, datetimes.astype("M8[s]"))
        del result
        assert probe.stats(policy) == (1, 1, 1, 0)
        del policy

    # Conversion errors occur after a result buffer exists; both errors and
    # partially filled buffers must unwind through the restored allocator.
    for invalid, error_type in ((np.array(["2026-09-11T12:00:00", "invalid"]), ValueError),
                                (np.array(["2026-09-11T12:00:00", 123], dtype=object), TypeError)):
        policy = probe.new_policy(-1)
        try:
            invoke(policy, _io.parse_datetimes, invalid)
        except error_type:
            pass
        else:
            raise AssertionError("invalid datetime input was accepted")
        assert probe.stats(policy) == (1, 1, 1, 0)
        del policy

    assert probe.current_policy() is original
    assert probe.policy_count() == 0
    gc.collect()
    print("NumPy allocator probe passed: 425 injected native failures; no retained data buffers or policies")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--leak-check", action="store_true")
    args = parser.parse_args()
    runtime = ctypes.CDLL(None)
    if args.leak_check:
        # Fail closed instead of claiming leak qualification without a runtime.
        enable_tracking = runtime.__lsan_enable
        disable_tracking = runtime.__lsan_disable
        leak_check = runtime.__lsan_do_recoverable_leak_check
        leak_check.restype = ctypes.c_int
        disable_tracking()
    else:
        enable_tracking = disable_tracking = lambda: None
    build(args.build_dir.resolve())
    exercise(args.build_dir.resolve(), enable_tracking, disable_tracking)
    if args.leak_check:
        gc.collect()
        sys.stdout.flush()
        os._exit(1 if leak_check() else 0)


if __name__ == "__main__":
    main()
