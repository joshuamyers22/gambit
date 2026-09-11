/* Test-only NEP 49 allocator. Never linked into Gambit or shipped in its wheel.
 * Use in a single-threaded disposable probe process, not application code. */
#define PY_SSIZE_T_CLEAN
#define NPY_TARGET_VERSION NPY_1_22_API_VERSION
#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <Python.h>
#include <numpy/arrayobject.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    PyDataMem_Handler handler;
    Py_ssize_t fail_at;
    size_t attempts, allocated, freed, failures;
    int used;
} Policy;

static size_t live_policies = 0;
static char policy_tag;

static int should_fail(Policy *policy) {
    size_t attempt = policy->attempts++;
    if (policy->fail_at >= 0 && attempt == (size_t)policy->fail_at) {
        ++policy->failures;
        return 1;
    }
    return 0;
}

static void *fault_malloc(void *ctx, size_t size) {
    Policy *policy = ctx;
    if (should_fail(policy)) return NULL;
    void *result = malloc(size ? size : 1);
    if (result) ++policy->allocated;
    return result;
}

static void *fault_calloc(void *ctx, size_t count, size_t size) {
    Policy *policy = ctx;
    if (should_fail(policy)) return NULL;
    if (size && count > SIZE_MAX / size) return NULL;
    size_t bytes = count * size;
    void *result = calloc(1, bytes ? bytes : 1);
    if (result) ++policy->allocated;
    return result;
}

static void *fault_realloc(void *ctx, void *pointer, size_t size) {
    Policy *policy = ctx;
    if (should_fail(policy)) return NULL;
    int new_allocation = pointer == NULL;
    /* A zero-byte request remains an owned, freeable allocation. */
    void *result = realloc(pointer, size ? size : 1);
    if (result && new_allocation) ++policy->allocated;
    return result;
}

static void fault_free(void *ctx, void *pointer, size_t size) {
    (void)size;
    Policy *policy = ctx;
    if (pointer) {
        if (policy->freed >= policy->allocated) abort();
        ++policy->freed;
        free(pointer);
    }
}

static Policy *get_policy(PyObject *capsule) {
    if (!PyCapsule_IsValid(capsule, "mem_handler") ||
        PyCapsule_GetContext(capsule) != &policy_tag) {
        PyErr_SetString(PyExc_TypeError, "expected this probe's memory policy");
        return NULL;
    }
    PyDataMem_Handler *handler = PyCapsule_GetPointer(capsule, "mem_handler");
    return handler->allocator.ctx;
}

static void destroy_policy(PyObject *capsule) {
    PyDataMem_Handler *handler = PyCapsule_GetPointer(capsule, "mem_handler");
    Policy *policy = handler->allocator.ctx;
    if (policy->allocated != policy->freed) abort();
    --live_policies;
    free(policy);
}

static PyObject *new_policy(PyObject *self, PyObject *arg) {
    (void)self;
    Py_ssize_t fail_at = PyLong_AsSsize_t(arg);
    if (PyErr_Occurred()) return NULL;
    if (fail_at < -1) {
        PyErr_SetString(PyExc_ValueError, "failure index must be -1 or nonnegative");
        return NULL;
    }
    Policy *policy = calloc(1, sizeof(*policy));
    if (!policy) return PyErr_NoMemory();
    policy->fail_at = fail_at;
    strcpy(policy->handler.name, "gambit_test_fault_allocator");
    policy->handler.version = 1;
    policy->handler.allocator = (PyDataMemAllocator){
        policy, fault_malloc, fault_calloc, fault_realloc, fault_free
    };
    PyObject *capsule = PyCapsule_New(&policy->handler, "mem_handler", destroy_policy);
    if (!capsule) { free(policy); return NULL; }
    ++live_policies;
    if (PyCapsule_SetContext(capsule, &policy_tag) < 0) {
        Py_DECREF(capsule);
        return NULL;
    }
    return capsule;
}

static PyObject *invoke(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule, *callable, *call_args, *kwargs;
    if (!PyArg_ParseTuple(args, "OOOO", &capsule, &callable, &call_args, &kwargs)) return NULL;
    Policy *policy = get_policy(capsule);
    if (!policy) return NULL;
    if (policy->used || !PyCallable_Check(callable) || !PyTuple_Check(call_args) ||
        !PyDict_Check(kwargs)) {
        PyErr_SetString(PyExc_ValueError, "need an unused policy, callable, tuple and dict");
        return NULL;
    }
    PyObject *previous = PyDataMem_SetHandler(capsule);
    if (!previous) return NULL;
    policy->used = 1;
    PyObject *result = PyObject_Call(callable, call_args, kwargs);
    PyObject *error_type, *error_value, *error_traceback;
    PyErr_Fetch(&error_type, &error_value, &error_traceback);
    PyObject *replaced = PyDataMem_SetHandler(previous);
    Py_DECREF(previous);
    if (!replaced) {
        Py_XDECREF(result);
        Py_XDECREF(error_type);
        Py_XDECREF(error_value);
        Py_XDECREF(error_traceback);
        return NULL;
    }
    Py_DECREF(replaced);
    PyErr_Restore(error_type, error_value, error_traceback);
    return result;
}

static PyObject *stats(PyObject *self, PyObject *capsule) {
    (void)self;
    Policy *policy = get_policy(capsule);
    if (!policy) return NULL;
    return Py_BuildValue("(KKKK)", (unsigned long long)policy->attempts,
        (unsigned long long)policy->allocated, (unsigned long long)policy->freed,
        (unsigned long long)policy->failures);
}

static PyObject *policy_count(PyObject *self, PyObject *unused) {
    (void)self;
    (void)unused;
    return PyLong_FromSize_t(live_policies);
}

static PyObject *current_policy(PyObject *self, PyObject *unused) {
    (void)self;
    (void)unused;
    return PyDataMem_GetHandler();
}

static PyMethodDef methods[] = {
    {"new_policy", new_policy, METH_O, "Create a one-call failure policy."},
    {"invoke", invoke, METH_VARARGS, "Invoke and restore the prior allocator, including on error."},
    {"stats", stats, METH_O, "Return attempts, allocations, frees and injected failures."},
    {"policy_count", policy_count, METH_NOARGS, "Count policy capsules still alive."},
    {"current_policy", current_policy, METH_NOARGS, "Return the active NumPy handler capsule."},
    {NULL, NULL, 0, NULL}
};
static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "_gambit_numpy_allocator", NULL, -1, methods,
    NULL, NULL, NULL, NULL
};
PyMODINIT_FUNC PyInit__gambit_numpy_allocator(void) {
    import_array();
    return PyModule_Create(&module);
}
