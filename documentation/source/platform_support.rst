Platform support
================

Gambit's supported native build platforms are Linux and macOS on Python 3.10
and 3.12. Continuous integration compiles the C++ and Cython extensions and
runs the test suite on each supported platform and Python version.

Native Windows builds are not currently supported. The I/O extension depends
on libzip, and the project does not yet provide reproducible MSVC library
discovery or a Windows dependency-installation workflow. Windows users should
run Gambit through WSL with ``libzip-dev`` installed.

Adding native Windows support requires a CI build that installs libzip, compiles
all three native extensions with MSVC, and passes the same test suite before
Windows can be listed as supported.
