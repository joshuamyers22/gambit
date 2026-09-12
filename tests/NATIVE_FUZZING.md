# Native CSV/ZIP and Python IPC fuzzing

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

Outputs retain `run.json`, `fuzz.log`, corpus, executable and failure artifacts; CI retains
failure outputs for seven days. Use synthetic inputs only: artifacts may contain
entire source files. Reproduce using the saved executable, set
`GAMBIT_FUZZ_FORMAT` to the original format and `GAMBIT_FUZZ_INPUT` to a disposable
file in a trusted scratch directory, and pass the artifact path as its argument.
Rebuild with equivalent compiler/sanitizers on another machine. Minimize findings
into checked-in regression tests/seeds.

The separate `native-fuzz.yml` workflow adds weekly Monday 04:17 UTC campaigns
and a manual trigger. Each CSV/ZIP job stops at one million executions or ten
minutes, with a fifteen-minute job backstop and the same per-input/RSS limits.
Its nonzero uint32 seed varies with the GitHub run ID and is recorded alongside
the source commit, compiler/run commands and sanitizer settings in `run.json`.
For local runs, `source_commit` is null unless `GITHUB_SHA` is set; record the
checkout and any local changes separately. Metadata is diagnostic provenance,
not an attestation that a worktree is clean.

The weekly workflow retains synthetic corpora, logs and failure inputs for seven
days whether the campaign passes or fails; it does not restore arbitrary remote
corpora, upload executables, request repository write permissions, or publish
packages. A parent timeout preserves and prints partial diagnostics and fails
the run. Long campaigns supplement the existing per-change CI fuzz checks; they
do not replace required checks or qualify HDF5/IPC. The workflow was merged through
[PR #32](https://github.com/joshuamyers22/gambit/pull/32). Its first manual
[extended run](https://github.com/joshuamyers22/gambit/actions/runs/34662061708)
used commit `c3c1864` and seed `302323349`: CSV passed 285,903 executions in
601 seconds (439 MiB peak reported RSS); ZIP failed after 223,457 executions
at 519 MiB, exceeding the 512 MiB threshold. The campaign is **not qualified**.
The ZIP log reports out-of-memory, not a demonstrated leak or corruption;
separate input-driven allocation, cumulative retention and sanitizer overhead
before choosing a fix. Do not simply raise the threshold to turn this run green.
Both artifacts are preserved locally at
`/private/tmp/gambit-extended-fuzz-evidence.ON0HDn`; GitHub retention is seven days.

On 2026-09-11 local seed replay reproduced signed overflow in `str_to_int32`;
six Python CSV/ZIP cases also failed against the prior extension. Checked
unsigned-magnitude parsing fixes i4/i8 and integer-backed datetime extrema and
overflow while preserving in-range prefix semantics. The installed Apple compiler
lacks libFuzzer. Short hosted CSV/ZIP campaigns have passed. Resolving the
extended ZIP memory failure, further scheduled runs and independent review
remain [P0.3](../PRODUCTION_READINESS_PLAN.md) work.

## Coverage-guided Python IPC preflight

`tools/run_ipc_fuzz.py` adds a separate target for Gambit's bounds-checked
`ipc_validation` module, using [Atheris](https://github.com/google/atheris).
The target and validator's Python functions/accessors are explicitly instrumented
in a fresh child process after trusted imports. This is Python bytecode coverage, **not native
Polars/Arrow decoder coverage or ASan/UBSan/LSan qualification**. Mutated IPC bytes
never go to `pl.read_ipc`; only trusted synthetic seed frames are serialized.

For a CPython 3.12 Linux x86-64 environment with frozen Gambit dependencies:

```sh
uv pip install --python .venv/bin/python --no-deps --require-hashes --only-binary :all: -r tests/requirements-ipc-fuzz.txt
.venv/bin/python tools/run_ipc_fuzz.py --output-dir /tmp/gambit-ipc-fuzz
```

The test-only Atheris 3.1.0 wheel is pinned by version and SHA-256, separately from
package runtime dependencies. Its available wheel determines this runner's
qualification platform. Other environments can explicitly run
`--replay-only` without Atheris; replay is never labeled coverage-guided. A missing
engine is an error for a requested campaign, not a silent fallback.

Nineteen generated seeds include malformed envelopes and empty/nonempty,
multi-batch numeric, nullable, string and binary IPC using both supported Polars
compatibility profiles. Two prefix bytes select the manifest schema and a bounded
row count; the rest is the IPC file. Reproducers include that prefix. The target
accepts only `BacktestBundleError` as an expected rejection; all other exceptions
are findings, and admitted decoded cost must stay within the configured budget.

Limits: 64 KiB total fuzz input, 64 rows, eight columns, sixteen record batches,
32 KiB total IPC metadata and 256 KiB decoded-payload estimate. Default campaigns
stop at 10,000 executions or thirty seconds, with five seconds per input, a
512 MiB libFuzzer RSS threshold and a parent timeout thirty seconds beyond the
campaign limit. These are diagnostic safeguards, not a filesystem sandbox or
hard production process-memory limit. Replay only has input/parser/parent bounds.

The per-change CI job keeps `run.json`, the synthetic corpus, failure inputs and
logs for seven days. The runner fails on child errors/timeouts or missing coverage
and completion markers, preserving partial diagnostics. No external corpus or
customer data is uploaded. HDF5 fuzzing, native decoder instrumentation and wider
schema profiles remain separate work; a short campaign cannot close P0.3 alone.

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
The existing broad interpreter suppressions
are deliberately not applied to this new probe because they could hide a leak
with NumPy array creation in its stack.

For actual Linux leak qualification, use a sanitized Gambit build and the
pre-interpreter launcher (the ordinary command above remains a counter-only test):

```sh
GAMBIT_SANITIZER_RUN=1 \
ASAN_LIBRARY="$(gcc -print-file-name=libasan.so)" \
CXX_LIBRARY="$(g++ -print-file-name=libstdc++.so.6)" \
python tests/run_numpy_leak_check.py --build-dir /tmp/gambit-numpy-lsan
```

The directory must not exist. This runner requires Linux, a shared CPython
development library, and matching GCC sanitizer libraries. It compiles the
test-only `native_lsan_python.c` launcher, which calls `__lsan_disable` **before**
[`Py_BytesMain`](https://docs.python.org/3.12/c-api/init.html#c.Py_BytesMain)
initializes Python. Dependency setup stays outside the tracked scope; each
native invocation is enabled and then disabled with balanced calls. Per the
[LSan interface](https://github.com/llvm/llvm-project/blob/main/compiler-rt/include/sanitizer/lsan_interface.h),
disable/enable scopes nest. Disabling again inside the probe would accidentally
leave native calls untracked, so a regression protects this ordering and direct
`--leak-check` calls without the launcher are rejected.

Two subprocesses must produce the expected results: the full 425-failure workload
must return zero leaks, and a separate deliberate-leak control must return a
16-byte report through `fault_malloc` / the native reader's NumPy allocation path.
The control deliberately omits one buffer free in the **test helper only** (its
free counter records the callback, not an actual deallocation for that control).
A crash, disabled checking, missing success marker or unrelated allocation report
fails the runner. No suppression patterns are used. Logs are retained in the
build directory and CI uploads them for seven days, including successful control
evidence. A positive-control LSan error in that artifact is intentional; an error
from the ordinary probe is not.

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
clean hosted qualification at that stage.

The follow-up [hosted run 34658154517](https://github.com/joshuamyers22/gambit/actions/runs/34658154517)
passed the corrected general lifetime probe. Only the independent NumPy probe
failed, reporting 1,086,365 bytes across 941 interpreter allocations despite its
passing data-allocation counters. Its Python-level disable happened too late to
exclude startup allocations. In an ARM64 Debian/GCC 12/CPython 3.12.11 container,
the original probe reproduced startup reports (1,092,763 bytes / 948 allocations),
while the pre-interpreter launcher passed unsuppressed with all 425 injected
failures and caught exactly the deliberate 16-byte NumPy leak. This verifies that
scoped Linux workload, not whole-interpreter leak freedom. The updated gate also
passed on hosted x86-64 in [PR #31's CI run](https://github.com/joshuamyers22/gambit/actions/runs/34659756969)
before merge to `main`. P0.3 still needs broader HDF5/IPC campaigns, resource
containment and independent security review; this does not close it alone.

CI now attempts the independent NumPy check whenever the native build succeeds,
even if the preceding stress probe fails, unless the run is cancelled. An earlier
failure still fails the job. Native boundary tests also cover the probe's own
lifetime and required-runtime behavior. Do not add a suppression merely to make
these checks green; preserve the allocation stack and distinguish live Python
state from resources retained after their documented lifetime.
