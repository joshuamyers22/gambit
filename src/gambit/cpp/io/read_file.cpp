// Native CSV/NumPy boundary. All owners unwind while holding the correct GIL state.
#define PY_SSIZE_T_CLEAN
#define NPY_NO_DEPRECATED_API 1
#include <Python.h>
#include <numpy/ndarrayobject.h>
#include <cstring>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>
#include <ctime>
#include "csv_reader.hpp"

#ifdef _MSC_VER
#include <iomanip>
#include <sstream>
static char* strptime(const char* value, const char* format, struct tm* result) {
    std::istringstream input(value);
    input >> std::get_time(result, format);
    return input.fail() ? nullptr : const_cast<char*>(value + input.tellg());
}
#endif

struct PythonDecref {
    void operator()(PyObject* value) const { Py_XDECREF(value); }
};
using PythonOwner = std::unique_ptr<PyObject, PythonDecref>;

class ReleasedGIL {
    PyThreadState* state;
public:
    ReleasedGIL(): state(PyEval_SaveThread()) {}
    ~ReleasedGIL() { PyEval_RestoreThread(state); }
    ReleasedGIL(const ReleasedGIL&) = delete;
    ReleasedGIL& operator=(const ReleasedGIL&) = delete;
};

static bool positive_limit(PyObject* value, size_t& result, const char* name) {
    if (!value) return true;
    if (!PyLong_Check(value) || PyBool_Check(value)) {
        PyErr_Format(PyExc_TypeError, "%s must be a positive integer", name);
        return false;
    }
    result = PyLong_AsSize_t(value);
    if (PyErr_Occurred()) return false;
    if (result == 0 || result > static_cast<size_t>(std::numeric_limits<Py_ssize_t>::max())) {
        PyErr_Format(PyExc_ValueError, "%s must be positive and fit in Py_ssize_t", name);
        return false;
    }
    return true;
}

static PythonOwner dtype_descriptor(const std::string& dtype) {
    PythonOwner text(PyUnicode_FromStringAndSize(dtype.data(), static_cast<Py_ssize_t>(dtype.size())));
    if (!text) return PythonOwner();
    PyArray_Descr* descriptor = nullptr;
    if (!PyArray_DescrConverter(text.get(), &descriptor)) return PythonOwner();
    return PythonOwner(reinterpret_cast<PyObject*>(descriptor));
}

static PythonOwner numpy_array(PythonOwner descriptor, size_t rows) {
    if (rows > static_cast<size_t>(std::numeric_limits<npy_intp>::max())) {
        PyErr_SetString(PyExc_OverflowError, "array row count exceeds npy_intp");
        return PythonOwner();
    }
    npy_intp dimension = static_cast<npy_intp>(rows);
    // NumPy owns its own allocation. NewFromDescr steals the descriptor on both
    // success and failure; no PyDataMem buffer or manual OWNDATA transfer.
    return PythonOwner(PyArray_NewFromDescr(&PyArray_Type,
        reinterpret_cast<PyArray_Descr*>(descriptor.release()), 1, &dimension,
        nullptr, nullptr, 0, nullptr));
}

static PyObject* read_file_impl(PyObject* args, PyObject* kwargs) {
    const char* filename = nullptr;
    PyObject* indices_object = nullptr;
    PyObject* dtypes_object = nullptr;
    const char* separator = ",";
    int skip_rows = 1;
    int max_rows = 0;
    PyObject* input_limit = nullptr;
    PyObject* output_limit = nullptr;
    PyObject* column_limit = nullptr;
    const char* names[] = {"filename", "col_indices", "dtypes", "separator",
        "skip_rows", "max_rows", "max_input_bytes", "max_output_bytes", "max_columns", nullptr};
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "sOO|siiOOO", const_cast<char**>(names),
            &filename, &indices_object, &dtypes_object, &separator, &skip_rows, &max_rows,
            &input_limit, &output_limit, &column_limit)) return nullptr;
    if (!PyList_Check(indices_object) || !PyList_Check(dtypes_object)) {
        PyErr_SetString(PyExc_RuntimeError, "col_indices and dtypes must be a list");
        return nullptr;
    }
    if (::strlen(separator) != 1) {
        PyErr_SetString(PyExc_ValueError, "separator must contain exactly one byte");
        return nullptr;
    }
    if (skip_rows < 0 || max_rows < 0) {
        PyErr_SetString(PyExc_RuntimeError, "skip_rows and max_rows must be >= 0");
        return nullptr;
    }
    CsvLimits limits;
    if (!positive_limit(input_limit, limits.max_input_bytes, "max_input_bytes") ||
        !positive_limit(output_limit, limits.max_output_bytes, "max_output_bytes") ||
        !positive_limit(column_limit, limits.max_columns, "max_columns")) return nullptr;
    const Py_ssize_t count = PyList_Size(indices_object);
    if (count == 0) {
        PyErr_SetString(PyExc_RuntimeError, "col_indices and dtypes must not be empty");
        return nullptr;
    }
    if (count != PyList_Size(dtypes_object)) {
        PyErr_SetString(PyExc_RuntimeError, "col_indices and dtypes must be same size");
        return nullptr;
    }
    if (static_cast<size_t>(count) > limits.max_columns) {
        PyErr_SetString(PyExc_RuntimeError, "CSV column limit exceeded");
        return nullptr;
    }
    std::vector<int> indices;
    std::vector<std::string> dtypes;
    std::vector<PythonOwner> descriptors;
    size_t row_width = 0;
    int previous = -1;
    for (Py_ssize_t i = 0; i < count; ++i) {
        PyObject* index = PyList_GetItem(indices_object, i);
        if (!PyLong_Check(index) || PyBool_Check(index)) {
            PyErr_SetString(PyExc_TypeError, "column indices must be integers");
            return nullptr;
        }
        const long value = PyLong_AsLong(index);
        if (PyErr_Occurred()) return nullptr;
        if (value > std::numeric_limits<int>::max() || value < std::numeric_limits<int>::min()) {
            PyErr_SetString(PyExc_OverflowError, "column index does not fit in a C int");
            return nullptr;
        }
        if (value <= previous) {
            PyErr_SetString(PyExc_RuntimeError, "col_indices must be monotonically increasing");
            return nullptr;
        }
        indices.push_back(static_cast<int>(value));
        previous = static_cast<int>(value);
        PyObject* type = PyList_GetItem(dtypes_object, i);
        if (!PyUnicode_Check(type)) {
            PyErr_SetString(PyExc_TypeError, "dtypes must contain strings");
            return nullptr;
        }
        if (PyUnicode_GetLength(type) > 64) {
            PyErr_SetString(PyExc_TypeError, "dtype exceeds the 64-character schema limit");
            return nullptr;
        }
        PythonOwner ascii(PyUnicode_AsASCIIString(type));
        if (!ascii) return nullptr;
        std::string dtype(PyBytes_AS_STRING(ascii.get()), static_cast<size_t>(PyBytes_GET_SIZE(ascii.get())));
        if (dtype.find('\0') != std::string::npos) {
            PyErr_SetString(PyExc_TypeError, "dtype cannot contain a NUL byte");
            return nullptr;
        }
        const size_t width = csv_itemsize(dtype);
        if (width > limits.max_output_bytes - row_width) {
            PyErr_SetString(PyExc_RuntimeError, "CSV output byte limit exceeded by schema");
            return nullptr;
        }
        row_width += width;
        PythonOwner descriptor = dtype_descriptor(dtype);
        if (!descriptor) return nullptr;
        descriptors.push_back(std::move(descriptor));
        dtypes.push_back(std::move(dtype));
    }

    std::vector<CsvColumn> columns;
    {
        ReleasedGIL released;
        read_csv(filename, indices, dtypes, separator[0], skip_rows, max_rows, columns, limits);
    }
    PythonOwner arrays(PyList_New(count));
    if (!arrays) return nullptr;
    for (Py_ssize_t i = 0; i < count; ++i) {
        CsvColumn& column = columns[static_cast<size_t>(i)];
        PythonOwner array = numpy_array(std::move(descriptors[static_cast<size_t>(i)]),
                                        column.values.size() / column.itemsize);
        if (!array) return nullptr;
        auto* ndarray = reinterpret_cast<PyArrayObject*>(array.get());
        if (static_cast<size_t>(PyArray_ITEMSIZE(ndarray)) != column.itemsize ||
            static_cast<size_t>(PyArray_NBYTES(ndarray)) != column.values.size()) {
            PyErr_SetString(PyExc_RuntimeError, "CSV/NumPy dtype size mismatch");
            return nullptr;
        }
        if (!column.values.empty()) ::memcpy(PyArray_DATA(ndarray), column.values.data(), column.values.size());
        PyList_SET_ITEM(arrays.get(), i, array.release());  // fresh, correctly sized list
        std::vector<unsigned char>().swap(column.values);
    }
    return arrays.release();
}

static PyObject* read_file(PyObject*, PyObject* args, PyObject* kwargs) {
    try {
        return read_file_impl(args, kwargs);
    } catch (const std::bad_alloc&) {
        return PyErr_NoMemory();
    } catch (const std::invalid_argument& error) {
        PyErr_SetString(PyExc_TypeError, error.what());
    } catch (const std::exception& error) {
        PyErr_SetString(PyExc_RuntimeError, error.what());
    } catch (...) {
        PyErr_SetString(PyExc_RuntimeError, "unknown native CSV reader failure");
    }
    return nullptr;
}

static PyObject* parse_datetimes_impl(PyObject* args) {
    PyObject* input = nullptr;
    if (!PyArg_ParseTuple(args, "O!", &PyArray_Type, &input)) return nullptr;
    const npy_intp size = PyArray_SIZE(reinterpret_cast<PyArrayObject*>(input));
    PythonOwner descriptor = dtype_descriptor("M8[s]");
    if (!descriptor) return nullptr;
    PythonOwner output = numpy_array(std::move(descriptor), static_cast<size_t>(size));
    if (!output) return nullptr;
    auto* values = static_cast<int64_t*>(PyArray_DATA(reinterpret_cast<PyArrayObject*>(output.get())));
    for (npy_intp i = 0; i < size; ++i) {
        PythonOwner item(PySequence_GetItem(input, i));
        if (!item) return nullptr;
        const char* text = PyUnicode_AsUTF8(item.get());
        if (!text) return nullptr;
        struct tm value = {};
        if (::strptime(text, "%Y-%m-%dT%H:%M:%S", &value) == nullptr) {
            PyErr_SetString(PyExc_ValueError, "datetime must match YYYY-MM-DDTHH:MM:SS");
            return nullptr;
        }
#ifdef _MSC_VER
        const time_t result = ::_mkgmtime(&value);
#else
        const time_t result = ::timegm(&value);
#endif
        if (result == static_cast<time_t>(-1)) {
            PyErr_SetString(PyExc_ValueError, "datetime is outside the supported UTC range");
            return nullptr;
        }
        values[i] = static_cast<int64_t>(result);
    }
    return output.release();
}

static PyObject* parse_datetimes(PyObject*, PyObject* args) {
    try {
        return parse_datetimes_impl(args);
    } catch (const std::bad_alloc&) {
        return PyErr_NoMemory();
    } catch (const std::exception& error) {
        PyErr_SetString(PyExc_RuntimeError, error.what());
        return nullptr;
    }
}

static PyMethodDef methods[] = {
    {"read_file", (PyCFunction)(void(*)(void))read_file, METH_VARARGS | METH_KEYWORDS,
     "Read CSV with finite max_input_bytes, max_output_bytes, and max_columns budgets."},
    {"parse_datetimes", parse_datetimes, METH_VARARGS, "Parse datetimes"},
    {nullptr, nullptr, 0, nullptr}
};
static PyModuleDef module = {PyModuleDef_HEAD_INIT, "_io", nullptr, -1, methods,
                            nullptr, nullptr, nullptr, nullptr};
PyMODINIT_FUNC PyInit__io(void) {
    import_array();
    return PyModule_Create(&module);
}
