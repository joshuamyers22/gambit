/* Test-only Linux launcher: exclude interpreter startup before it allocates.
 * The probe enables tracking around each native call. No stack suppressions. */
#include <Python.h>
#include <sanitizer/lsan_interface.h>
#include <stdio.h>

static int bootstrapped = 0;

/* Exported so the Python probe cannot silently run with a late/double disable. */
int gambit_lsan_bootstrapped(void) {
    return bootstrapped;
}

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: native-lsan-python PYTHON SCRIPT [ARGS...]\n");
        return 2;
    }
    __lsan_disable();
    bootstrapped = 1;
    /* Preserve the active interpreter's argv[0], including its venv location. */
    return Py_BytesMain(argc - 1, argv + 1);
}
