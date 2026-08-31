Platform support
================

Gambit's supported native build platforms are Linux and macOS on Python 3.10,
3.11, and 3.12. Continuous integration compiles the C++ and Cython extensions and
runs the test suite on each supported platform and Python version.

Release artifacts include manylinux x86-64 wheels and macOS x86-64 and ARM64
wheels. Linux wheels are repaired with ``auditwheel`` and macOS wheels with
``delocate`` so libzip is included rather than referenced from a package-manager
path. The binary wheels target macOS 15 or newer on both x86-64 and ARM64
because the Homebrew libraries bundled by the supported hosted builders have
that minimum deployment target. Source distributions remain available for
other compatible POSIX systems with a suitable native toolchain and libzip.

Native Windows builds are not currently supported. The I/O extension depends
on libzip, and the project does not yet provide reproducible MSVC library
discovery or a Windows dependency-installation workflow. Windows users should
run Gambit through WSL with ``libzip-dev`` installed.

Adding native Windows support requires a CI build that installs libzip, compiles
all three native extensions with MSVC, and passes the same test suite before
Windows can be listed as supported.
