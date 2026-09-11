# Native CSV/ZIP fuzzing

`tools/run_native_fuzz.py` compiles `native_csv_fuzz.cpp` and the production CSV
reader with [LLVM libFuzzer](https://llvm.org/docs/LibFuzzer.html), ASan and UBSan.
Coverage feedback guides input mutation; the older `boundary_fuzz_probe.py` is
deterministic smoke, not coverage-guided fuzzing.

Run from the repository with Python 3.10+, Clang including its libFuzzer runtime,
and libzip development headers/libraries:

```sh
python tools/run_native_fuzz.py --format csv --output-dir /tmp/gambit-fuzz-csv
python tools/run_native_fuzz.py --format zip --output-dir /tmp/gambit-fuzz-zip
```

Output directories must be new. Set `FUZZ_CXX` for another Clang and
`LIBZIP_PREFIX` for nonstandard libzip paths. No Python extension is imported.
macOS Command Line Tools may lack libFuzzer: use a suitable LLVM or
`--replay-only` for ASan/UBSan seeds. Replay is **not** fuzz qualification.

Synthetic seeds cover integer extrema/overflow, long tokens, byte/line endings,
strings and floats. ZIP seeds wrap the same payloads in deflated `data.csv`
members. The target checks six single-column dtypes, shape and byte ceilings;
it is not exhaustive schema/separator/datetime coverage.

Defaults: 10,000 executions or 30 seconds, 64 KiB input, 4 KiB output, 256 rows,
five-second per-input timeout and 512 MiB RSS failure threshold. A parent timeout
is an additional backstop. Replay has the reader budgets and parent timeout, not
libFuzzer's RSS guard. These are test controls, not production process limits.
Linux enables LeakSanitizer; macOS disables unsupported leak checking. System
libzip is not instrumented. Python/NumPy, HDF5 and IPC are outside this target.

Outputs retain `fuzz.log`, corpus, executable and failure artifacts; CI retains
failure outputs for seven days. Use synthetic inputs only: artifacts may contain
entire source files. Reproduce using the saved executable, set
`GAMBIT_FUZZ_FORMAT` to the original format and `GAMBIT_FUZZ_INPUT` to a disposable
file in a trusted scratch directory, and pass the artifact path as its argument.
Rebuild with equivalent compiler/sanitizers on another machine. Minimize findings
into checked-in regression tests/seeds.

On 2026-09-11 local seed replay reproduced signed overflow in `str_to_int32`;
six Python CSV/ZIP cases also failed against the prior extension. Checked
unsigned-magnitude parsing fixes i4/i8 and integer-backed datetime extrema and
overflow while preserving in-range prefix semantics. The installed Apple compiler
lacks libFuzzer. Hosted campaigns, longer scheduled runs and independent review
remain [P0.3](../PRODUCTION_READINESS_PLAN.md) work.

## NumPy allocation-failure probe

The separate `native_numpy_allocator_probe.py` compiles the test-only
`native_numpy_allocator.c` extension in a fresh directory and installs a scoped
[NumPy data-memory handler](https://numpy.org/doc/stable/reference/c-api/data_memory.html).
The handler fails one selected array-data allocation, counts allocations/frees,
and restores the previous policy on success or exception. Each array retains
its handler until its buffer is released. It never changes the installed Gambit
extension or ships in release artifacts.

```sh
python tests/native_numpy_allocator_probe.py --build-dir /tmp/gambit-numpy-allocator
```

Run in a disposable, single-threaded process with NumPy and Gambit installed,
a C compiler, and matching Python/NumPy development headers. The directory must
not already exist. The native pytest suite launches this probe as a subprocess.

The local probe injects 425 failures across four-column plain/ZIP reads (empty
and nonempty) and datetime parsing. It verifies partial-result cleanup, retries,
owned-array values, conversion-error cleanup, restored handler identity and
view lifetimes after caller policy references are dropped. Independent empty/zero
array controls confirm the fault handler is active and live buffers are counted.
No production behavior change was needed to pass these checks.

These are **array-data allocation** failures, not exhaustive Python-object,
dtype-descriptor, libzip-internal or system OOM injection. Local counts are not a
LeakSanitizer run. CI adds an ASan/UBSan-built helper and an unsuppressed Linux
`--leak-check` run, tracking native calls after interpreter/dependency setup.
`--leak-check` fails when no LSan runtime is present; it never silently downgrades.
Hosted execution remains pending. The existing broad interpreter suppressions
are deliberately not applied to this new probe because they could hide a leak
with NumPy array creation in its stack.

## Lifetime-check ordering and Linux diagnosis

`native_memory_probe.py` now executes its workload in a separate function that
returns before collection/leak checking. The original probe still held its final
CSV/ZIP arrays at the check; the new weak-reference regression fails against that
original ordering and verifies their destruction before the checker is invoked.
`--require-lsan` makes a missing runtime an error, and `--iterations N` permits
smaller reproductions without changing the default stress workload.

The hosted 16-byte `default_malloc` report was reproduced on x86-64 Linux with
NumPy 2.5.2. A deeper unsuppressed stack traced it through `PyDataMem_UserNEW`,
`PyArray_NewFromDescr` and Gambit's `numpy_array`. That specific report disappears
when the workload returns before the check. No production deallocator change
or additional suppression was made.

The diagnostic containers used Debian/GCC 12/CPython 3.12.11, not GitHub's exact
GCC 13/CPython 3.12.14 image. x86 emulation additionally required Polars 1.44.1's
matching compatibility runtime, installed only in the disposable container.
The stripped container Python library produced separate interpreter-retention
reports that its symbols could not match to the existing suppression list.
The independent NumPy data-allocation counters passed, but its unsuppressed
container LSan run also reported interpreter allocations. Neither run is a
clean hosted qualification; those residual reports still require triage.

CI now attempts the independent NumPy check whenever the native build succeeds,
even if the preceding stress probe fails, unless the run is cancelled. An earlier
failure still fails the job. Native boundary tests also cover the probe's own
lifetime and required-runtime behavior. Do not add a suppression merely to make
these checks green; preserve the allocation stack and distinguish live Python
state from resources retained after their documented lifetime.
