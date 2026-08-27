//
//  csv_reader.cpp
//  py_c_test
//
//  Created by Sal Abbasi on 9/12/22.
//

#include "csv_reader.hpp"
#include <cmath>
#include <algorithm>
#include <fstream>
#include <iostream>
#include <memory>
#include <vector>
#include <zip.h>
#include <math.h>
#include <string.h>
#include "utils.hpp"


// Windows uses _strdup instead of non-standard strdup function
#ifdef _MSC_VER
    #define strdup _strdup
    #define _CRT_SECURE_NO_WARNINGS
    #include <BaseTsd.h>
    typedef SSIZE_T ssize_t;
#endif

using namespace std;

static const float NANF = nanf("");
static const double NAND = nan("");

string get_error(int err_num) {
    char errmsg[255];
#ifdef _MSC_VER
    ::strerror_s(errmsg, 255, err_num);
#elif defined(__GLIBC__) && defined(_GNU_SOURCE)
    return string(::strerror_r(err_num, errmsg, 255));
#else
    if (::strerror_r(err_num, errmsg, 255) != 0) return "unknown system error";
#endif
    return string(errmsg);
}

vector<char*> tokenize_line(char *s, char delim, const vector<int>& col_indices) {
    vector<char*> ret;
    ret.reserve(16);
    size_t col_idx = 0;
    char* begin = s;
    size_t curr_col_idx = 0;
    size_t size = ::strlen(s);
    s[size] = delim;  // replace last \0 with delim so we can tokenize last column
    for (size_t i = 0; i < size + 1; ++i) {
        if (s[i] == delim) {
            s[i] = '\0';
            if (col_indices[col_idx] == static_cast<int>(curr_col_idx)) {
                ret.push_back(begin);
                col_idx += 1;
                if (col_idx == col_indices.size()) break;
            }
            begin = s + i + 1;
            curr_col_idx += 1;
        }
    }
    return ret;
}

float str_to_float(const char* str, char decimal_point, char thousands_separator) {
    // convert a string to a float
    float result = 0;
    bool zero = false;
    float sign = *str == '-' ? static_cast<void>(str++), -1.0f : 1.0f;
    if (*str == '0') zero = true;
    while ((*str >= '0' && *str <= '9') || (*str == thousands_separator)) {
        if (*str == thousands_separator) {
            str++;
            continue;
        }
        result *= 10;
        result += *str - '0';
        str++;
    }
    if (!zero && (result == 0)) return NANF;

    float multiplier = 0.1f;
    if (*str == decimal_point) {
        str++;
        while (*str >= '0' && *str <= '9') {
            result += (*str - '0') * multiplier;
            multiplier /= 10;
            str++;
        }
    }

    float power = 0.0f;
    result *= sign;
    if (*str == 'e' || *str == 'E') {
        str++;
        float powerer = *str == '-'? static_cast<void>(str++), 0.1f : 10.0f;

        while ((*str >= '0') && (*str <= '9')) {
            power *= 10;
            power += *str - '0';
            str++;
        }
        result *= pow(powerer, power);
    }
    return result;
}


double str_to_double(const char* str, char decimal_point, char thousands_separator) {
    // convert a string to a float
    double result = 0;
    bool zero = false;
    float sign = *str == '-' ? static_cast<void>(str++), -1.0f : 1.0f;
    if (*str == '0') zero = true;
    while ((*str >= '0' && *str <= '9') || (*str == thousands_separator)) {
        if (*str == thousands_separator) {
            str++;
            continue;
        }
        result *= 10;
        result += *str - '0';
        str++;
    }
    if (!zero && (result == 0)) return NAND;

    float multiplier = 0.1f;
    if (*str == decimal_point) {
        str++;
        while (*str >= '0' && *str <= '9') {
            result += (*str - '0') * multiplier;
            multiplier /= 10;
            str++;
        }
    }

    float power = 0.0f;
    result *= sign;
    if (*str == 'e' || *str == 'E') {
        str++;
        float powerer = *str == '-'? static_cast<void>(str++), 0.1f : 10.0f;

        while ((*str >= '0') && (*str <= '9')) {
            power *= 10;
            power += *str - '0';
            str++;
        }
        result *= pow(powerer, power);
    }
    return result;
}

int32_t str_to_int32(const char* str, char thousands_separator) {
    // convert a string to a int
    int result = 0;
    int sign = *str == '-' ? static_cast<void>(str++), -1 : 1;
    while ((*str >= '0' && *str <= '9') || (*str == thousands_separator)) {
        if (*str == thousands_separator) {
            str++;
            continue;
        }
        result *= 10;
        result += *str - '0';
        str++;
    }
    result *= sign;
    return result;
}

int64_t str_to_int64(const char* str, char thousands_separator) {
    // convert a string to a int
    int64_t result = 0;
    int sign = *str == '-' ? static_cast<void>(str++), -1 : 1;
    while ((*str >= '0' && *str <= '9') || (*str == thousands_separator)) {
        if (*str == thousands_separator) {
            str++;
            continue;
        }
        result *= 10;
        result += *str - '0';
        str++;
    }
    result *= sign;
    return result;
}

int8_t str_to_int8(const char* str) {
    // convert a string to a int
    auto len = strlen(str);
    if (len == 0) return 0;
    if (len == 4) {
        if (strcmp(str, "true") == 0) return 1;
        if (strcmp(str, "TRUE") == 0) return 1;
        if (strcmp(str, "True") == 0) return 1;
    }
    if (len == 5) {
        if (strcmp(str, "false") == 0) return 0;
        if (strcmp(str, "FALSE") == 0) return 0;
        if (strcmp(str, "False") == 0) return 0;
    }
    int8_t result = 0;
    int sign = *str == '-' ? static_cast<void>(str++), -1 : 1;
    while (*str >= '0' && *str <= '9') {
        result *= 10;
        result += *str - '0';
        str++;
    }
    result *= sign;
    return result;
}

template<typename T> T parse_string(const char* str) {
    return std::string(str);
}

template<> int32_t parse_string<int32_t>(const char* str) {
    return str_to_int32(str, ',');
}

template<> int64_t parse_string<int64_t>(const char* str) {
    return str_to_int64(str, ',');
}

template<> float parse_string<float>(const char* str) {
    return str_to_float(str, '.', ',');
}

template<> double parse_string<double>(const char* str) {
    return str_to_double(str, '.', ',');
}

template<> int8_t parse_string<int8_t>(const char* str) {
    return str_to_int8(str);
}

template<typename T> void add_value(const char* str, void* column) {
    T elem = parse_string<T>(str);
    auto vec = static_cast<vector<T>*>(column);
    vec->push_back(elem);
}

void add_line(const vector<char*>& fields, const vector<string>& dtypes, vector<void*>& data) {
    for (size_t i=0; i < dtypes.size(); ++i) {
        if (dtypes[i] == "f4") {
            add_value<float>(fields[i], data[i]);
        } else if (dtypes[i] == "f8") {
            add_value<double>(fields[i], data[i]);
        } else if (dtypes[i] == "i1") {
            add_value<int8_t>(fields[i], data[i]);
        } else if (dtypes[i] == "i4") {
            add_value<int32_t>(fields[i], data[i]);
        } else if (dtypes[i] == "i8") {
            add_value<int64_t>(fields[i], data[i]);
        } else if (dtypes[i].substr(0, 3) == "M8[") {
            add_value<int64_t>(fields[i], data[i]);
        } else if (!dtypes[i].empty() && dtypes[i][0] == 'S') {
            add_value<string>(fields[i], data[i]);
        } else {
            error("invalid type: " << dtypes[i] << " expected i1, i4, i8, f4, f8, M8[*] or S[n]");
        }
    }
}

template<typename T> vector<T>* create_vec(size_t max_rows) {
    auto vec = new vector<T>();
    vec->reserve(max_rows);
    return vec;
}

void* create_vector(const std::string& dtype, size_t max_rows) {
    if (dtype == "f4") {
        return create_vec<float>(max_rows);
    } else if (dtype == "f8") {
        return create_vec<double>(max_rows);
    } else if (dtype == "i1") {
        return create_vec<int8_t>(max_rows);
    } else if (dtype == "i4") {
        return create_vec<int32_t>(max_rows);
    } else if (dtype == "i8") {
        return create_vec<int64_t>(max_rows);
    } else if (dtype.substr(0, 3) == "M8[") {
        return create_vec<int64_t>(max_rows);
    } else if (!dtype.empty() && dtype[0] == 'S') {
        return create_vec<string>(max_rows);
    } else {
        error("invalid type: " << dtype << " expected i1, i4, i8, f4, f8, M8[*] or S[n]");
    }
}

void delete_vector(const std::string& dtype, void* data) {
    if (!data) return;
    if (dtype == "f4") {
        delete static_cast<vector<float>*>(data);
    } else if (dtype == "f8") {
        delete static_cast<vector<double>*>(data);
    } else if (dtype == "i1") {
        delete static_cast<vector<int8_t>*>(data);
    } else if (dtype == "i4") {
        delete static_cast<vector<int32_t>*>(data);
    } else if (dtype == "i8" || dtype.substr(0, 3) == "M8[") {
        delete static_cast<vector<int64_t>*>(data);
    } else if (!dtype.empty() && dtype[0] == 'S') {
        delete static_cast<vector<string>*>(data);
    }
}

void delete_output(const vector<string>& dtypes, vector<void*>& output) {
    for (size_t i = 0; i < output.size(); ++i) {
        delete_vector(dtypes[i], output[i]);
        output[i] = nullptr;
    }
}

struct Reader {
    virtual ssize_t getline(char** line) = 0;
    virtual string filename() = 0;
    virtual ssize_t fread(char* data, size_t length) = 0;
    virtual ~Reader() {}
};

static const size_t BUF_SIZE = 64 * 1024;
static const size_t MAX_LINE_SIZE = 16 * 1024 * 1024;
static const zip_uint64_t MAX_ZIP_MEMBER_SIZE = 1024ULL * 1024 * 1024;

ssize_t get_index(char* buf, size_t n, char c) {
    for (size_t i = 0; i < n; ++i) {
        if (buf[i] == c) return i;
    }
    return -1;
}

ssize_t read_line(char** buf, size_t* buf_size, size_t* begin_idx, char** line, Reader* reader) {
    if (!*buf) {
        *buf = static_cast<char*>(::malloc(BUF_SIZE));
        if (!*buf) error("could not allocate CSV read buffer");
        *begin_idx = 0;
        *buf_size = 0;
    }

    for (;;) {
        size_t available = *buf_size - *begin_idx;
        ssize_t newline_idx = get_index(*buf + *begin_idx, available, '\n');
        if (newline_idx >= 0) {
            size_t line_size = static_cast<size_t>(newline_idx);
            size_t next_line_idx = *begin_idx + line_size + 1;
            if (line_size > 0 && (*buf)[*begin_idx + line_size - 1] == '\r') {
                line_size--;
            }
            (*buf)[*begin_idx + line_size] = '\0';
            *line = *buf + *begin_idx;
            *begin_idx = next_line_idx;
            return static_cast<ssize_t>(line_size);
        }

        if (*begin_idx > 0 && available > 0) {
            ::memmove(*buf, *buf + *begin_idx, available);
        }
        *begin_idx = 0;
        *buf_size = available;

        if (available >= MAX_LINE_SIZE) {
            error(reader->filename() << " contains a row larger than the 16 MiB input limit");
        }

        size_t read_size = std::min(BUF_SIZE, MAX_LINE_SIZE - available);
        char* resized = static_cast<char*>(::realloc(*buf, available + read_size + 1));
        if (!resized) error("could not grow CSV read buffer");
        *buf = resized;

        ssize_t bytes_read = reader->fread(*buf + available, read_size);
        if (bytes_read < 0) return bytes_read;
        if (bytes_read == 0) {
            if (available == 0) return -1;
            (*buf)[available] = '\0';
            *line = *buf;
            *begin_idx = available;
            return static_cast<ssize_t>(available);
        }
        *buf_size = available + static_cast<size_t>(bytes_read);
    }
}


class ZipReader: public Reader {
public:
    ZipReader(const std::string& filename):
    _filename(filename),
    _zip_archive(nullptr),
    _zip_file(nullptr),
    _buf(nullptr),
    _buf_idx(0),
    _buf_size(0) {
        std::size_t i = filename.find(':');
        auto zip_filename = filename.substr(0, i);
        auto inner_filename = filename.substr(i + 1);
        int zip_error_code = 0;
        _zip_archive = zip_open(zip_filename.c_str(), ZIP_RDONLY, &zip_error_code);
        if (!_zip_archive) {
            zip_error_t zip_error;
            zip_error_init_with_code(&zip_error, zip_error_code);
            const string message = zip_error_strerror(&zip_error);
            zip_error_fini(&zip_error);
            error("can't read: " << zip_filename << " : " << message);
        }
        zip_stat_t member_stat;
        zip_stat_init(&member_stat);
        if (zip_stat(_zip_archive, inner_filename.c_str(), ZIP_FL_ENC_GUESS, &member_stat) != 0) {
            const string message = zip_strerror(_zip_archive);
            zip_close(_zip_archive);
            _zip_archive = nullptr;
            error("can't inspect " << inner_filename << " from " << filename << " : " << message);
        }
        if ((member_stat.valid & ZIP_STAT_SIZE) && member_stat.size > MAX_ZIP_MEMBER_SIZE) {
            zip_close(_zip_archive);
            _zip_archive = nullptr;
            error(inner_filename << " from " << filename << " exceeds the 1 GiB decompressed member limit");
        }
        _zip_file = zip_fopen(_zip_archive, inner_filename.c_str(), ZIP_FL_ENC_GUESS);
        if (!_zip_file) {
            const string message = zip_strerror(_zip_archive);
            zip_close(_zip_archive);
            _zip_archive = nullptr;
            error("can't read " << inner_filename << " from " << filename << " : " << message);
        }
    }

    string filename() override { return _filename; }

    ssize_t getline(char** line) override {
        return read_line(&_buf, &_buf_size, &_buf_idx, line, this);
    }

    ssize_t fread(char* buf, size_t buf_size) override {
        zip_int64_t bytes_read = zip_fread(_zip_file, buf, buf_size);
        if (bytes_read < 0) error("error reading " << _filename << " : " << zip_file_strerror(_zip_file));
        return static_cast<ssize_t>(bytes_read);
    }

    ~ZipReader() {
        if (_zip_file) zip_fclose(_zip_file);
        _zip_file = nullptr;
        if (_zip_archive) zip_close(_zip_archive);
        _zip_archive = nullptr;
        if (_buf) ::free(_buf);
    }

private:
    string _filename;
    zip_t* _zip_archive;
    zip_file_t* _zip_file;
    char* _buf;
    size_t _buf_idx;
    size_t _buf_size;
};

class FileReader: public Reader {
public:
    FileReader(const std::string& filename):
        _filename(filename),
        _file(::fopen(filename.c_str(), "r")),
        _buf(nullptr),
        _buf_idx(0),
        _buf_size(0)
    {
        if (!_file) error("can't read: " << filename << " : " << get_error(errno));
    }

    string filename() override {
        return _filename;
    }

    ssize_t getline(char** line) override {
        return read_line(&_buf, &_buf_size, &_buf_idx, line, this);
    }

    ssize_t fread(char* buf, size_t buf_size) override {
        size_t elems_read = ::fread(buf, sizeof(char), ::floor(buf_size / sizeof(char)), _file);
        if (elems_read == 0 && ferror(_file)) error("error reading file");
        return elems_read * sizeof(char);
    }

    ~FileReader() {
        if (_file) ::fclose(_file);
        _file = nullptr;
        if (_buf) ::free(_buf);
    }

private:
    string _filename;
    FILE* _file;
    char* _buf;
    size_t _buf_idx;
    size_t _buf_size;
};



bool read_csv_file(Reader* reader,
                   const std::vector<int>& col_indices,
                   const std::vector<std::string>& dtypes,
                   char separator,
                   int skip_rows,
                   int max_rows,
                   vector<void*>& output) {

    int row_num = 0;
    int data_row_count = 0;
    output.resize(dtypes.size());
    for (size_t i = 0; i < dtypes.size(); ++i) {
        output[i] = create_vector(dtypes[i], max_rows);
    }

    bool more_to_read = true;
    for (;;) {
        char* line = nullptr;
        ssize_t line_size = reader->getline(&line);
        if (line_size <= 0) {
            //eof or error.  ::getline returns zero in both cases, zip_fread returns -1 for error, 0 for eof
            more_to_read = false;
            break;
        }
        // cout << "row num: " << row_num << " len: " << strlen(line) << " " << line << endl;
        row_num++;

        if (row_num <= skip_rows) continue;

        if ((max_rows != 0) && (data_row_count >= max_rows)) break;
        auto fields = tokenize_line(line, separator, col_indices);
        if (!fields.size()) continue; // empty line
        if (fields.size() != dtypes.size()) {
            //replace nulls we added with separator so we can print out the line
            string _line(line, line_size);
            std::replace(_line.begin(), _line.end(), '\0', separator);
            error(reader->filename() << " found " << fields.size() << " " << " fields on row: " << row_num
                  << " line: " << _line << " but dtypes arg length was " << dtypes.size() << endl)
        }
        add_line(fields, dtypes, output);
        data_row_count++;
    }
    return more_to_read;
}

void test_csv_reader() {
    ifstream istr("/Users/sal/tmp/test.csv", ios_base::in);
    auto dtypes = vector<string>{
        "M8[ms]",
        "S10",
        "i4",
        "f8",
        "i1"};
    vector<void*> output(dtypes.size());
    bool more_to_read = false;
    auto vec1 = reinterpret_cast<vector<string>*>(output[0]);
    auto vec2 = reinterpret_cast<vector<string>*>(output[1]);
    cout << "row1: " << (*vec1)[0] << " " << (*vec2)[0] << "\n"
         << "row2: " << (*vec1)[1] << " " << (*vec2)[1] << "\n"
         << "more_to_read: " << more_to_read << endl;
    istr.close();
}



bool read_csv(const std::string& filename,
              const std::vector<int>& col_indices,
              const std::vector<std::string>& dtypes,
              char separator,
              int skip_rows,
              int max_rows,
              std::vector<void*>& output) {
    bool more_to_read = false;
    std::size_t i = filename.find(':');
    unique_ptr<Reader> reader;
    if (i == filename.npos) {
        reader.reset(new FileReader(filename));
    } else {
        reader.reset(new ZipReader(filename));
    }
    try {
        bool tmp = read_csv_file(reader.get(), col_indices, dtypes, separator, skip_rows, max_rows, output);
        if (tmp) more_to_read = true;
    } catch (...) {
        delete_output(dtypes, output);
        throw;
    }
    return more_to_read;
}


void test_csv_reader2() {
    cout << "starting" << endl;
    vector<void*> output(2);
    bool more_to_read = read_csv("/Users/sal/tmp/test.csv",
                                 {15, 18, 20},
                                 {"f4", "f4", "i4"},
                                 ',',
                                 1,
                                 0,
                                 output);
    auto vec1 = static_cast<vector<float>*>(output[0]);
    cout << "num_cols: " << output.size() << " num rows: " << vec1->size() << " more_to_read: " << more_to_read
         << " first entry: " << (*vec1)[0] << endl;
}

void test_csv_reader_zip() {
    for (int j=0; j < 100000; ++j) {
        cout << "starting" << endl;
        vector<void*> output(2);
        bool more_to_read = read_csv("/Users/sal/tmp/algo/20220316.zip:20220316/A/AAPL.csv",
                                     {2, 9, 18, 27, 35, 48, 49},
                                     {"S5", "f4", "f4", "f4", "f4", "i4", "i4"},
                                     ',',
                                     1,
                                     0,
                                     output);
        auto vec1 = static_cast<vector<float>*>(output[1]);
        cout << "num_cols: " << output.size() << " num rows: " << vec1->size() << " more_to_read: " << more_to_read
        << " first entry: " << (*vec1)[0] << endl;
        delete static_cast<vector<string>*>(output[0]);
        for (size_t i=1; i < output.size(); ++i) {
            if (i != 0 && i != 5) {
                delete static_cast<vector<float>*>(output[i]);
            }
        }
        delete static_cast<vector<int32_t>*>(output[5]);
    }
    cout << "done" << endl;
}
