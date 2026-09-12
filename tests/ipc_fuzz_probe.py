"""Isolated IPC-preflight Atheris worker, or explicitly labeled seed replay."""

import inspect
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--replay-only":
        from ipc_fuzz_target import MAX_INPUT_BYTES, test_one_input

        if len(sys.argv) < 3:
            raise SystemExit("seed replay requires at least one input")
        for filename in sys.argv[2:]:
            with Path(filename).open("rb") as source:
                test_one_input(source.read(MAX_INPUT_BYTES + 1))
        print(f"IPC seed replay passed: {len(sys.argv) - 2} inputs (NOT coverage-guided)")
        return

    import atheris

    from gambit import ipc_validation
    from ipc_fuzz_target import test_one_input

    # instrument_imports(include=...) is not an exclusive allowlist. Instrument
    # just this validator's Python functions and accessors after trusted imports.
    functions = [test_one_input]
    for owner in (ipc_validation, ipc_validation._Metadata, ipc_validation._Table):
        functions.extend(function for function in vars(owner).values()
                         if inspect.isfunction(function) and function.__module__ == ipc_validation.__name__)
    for function in functions:
        atheris.instrument_func(function)
    print(f"Instrumented {len(functions)} IPC preflight functions", flush=True)

    atheris.Setup(sys.argv, test_one_input, internal_libfuzzer=True)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
