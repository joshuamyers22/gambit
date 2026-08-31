Testing and validation
======================

Gambit separates tests by the boundary they exercise. The default command still
runs the complete suite::

   python -m pytest

For a faster development loop, select one suite with a pytest marker::

   python -m pytest -m unit
   python -m pytest -m integration
   python -m pytest -m "native and not fuzz and not performance"
   python -m pytest -m fuzz
   python -m pytest -m performance

``unit`` tests are isolated deterministic checks. ``integration`` tests cross
process, persistence, package, or executable-example boundaries. ``native``
tests require the compiled Cython or C++ extensions. ``fuzz`` tests run bounded,
reproducible hostile-input probes in subprocesses. ``performance`` tests verify
that benchmark harnesses work; timing results are measurements rather than
portable pass/fail thresholds.

Continuous integration runs unit tests on every supported Python and operating
system combination. Integration tests run on the reference Linux environment,
while native correctness runs on both Linux and macOS. Fuzz probes also run
under ASan and UBSan. Performance testing is an opt-in, non-blocking GitHub
Actions workflow so noisy hosted-runner timing does not block releases.

Adding tests
------------

New tests are assigned ``unit`` unless their module appears in the centralized
suite manifest in ``tests/conftest.py`` or carries an explicit registered
marker. Add boundary-crossing modules to that manifest. Benchmark modules must
end in ``_benchmark.py`` so they are automatically placed in the performance
suite. Never use an unregistered marker: pytest's strict marker configuration
turns that mistake into an error.
