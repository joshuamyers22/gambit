//
//  csv_reader.cpp
//  py_c_test
//
//  Created by Sal Abbasi on 9/12/22.
//

#include "csv_reader.hpp"
#include <cmath>
#include <cstdint>
#include <algorithm>
#include <fstream>
#include <iostream>
#include <memory>
#include <limits>
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

template<typename T> T checked_integer(const char* str, char thousands_separator) {
    // Accumulate magnitude unsigned: the signed minimum has no positive signed
    // counterpart. Retain legacy separator/prefix semantics, but never overflow.
    const bool negative = *str == '-';
    if (negative) ++str;
    const uint64_t maximum = static_cast<uint64_t>(std::numeric_limits<T>::max());
    const uint64_t limit = maximum + static_cast<uint64_t>(negative);
    uint64_t result = 0;
    while ((*str >= '0' && *str <= '9') || (*str == thousands_separator)) {
        if (*str == thousands_separator) {
            str++;
            continue;
        }
        const uint64_t digit = static_cast<uint64_t>(*str - '0');
        if (result > (limit - digit) / 10) error("CSV integer value out of range");
        result = result * 10 + digit;
        str++;
    }
    if (negative && result == maximum + 1) return std::numeric_limits<T>::min();
    const T value = static_cast<T>(result);
    return negative ? -value : value;
}

int32_t str_to_int32(const char* str, char thousands_separator) {
    return checked_integer<int32_t>(str, thousands_separator);
}

int64_t str_to_int64(const char* str, char thousands_separator) {
    return checked_integer<int64_t>(str, thousands_separator);
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

size_t csv_itemsize(const string& dtype) {
    if (dtype == "i1") return 1;
    if (dtype == "i4" || dtype == "f4") return 4;
    if (dtype == "i8" || dtype == "f8" || (dtype.size() > 4 && dtype.substr(0, 3) == "M8[" && dtype.back() == ']')) return 8;
    if (!dtype.empty() && dtype[0] == 'S') {
        size_t width = 0;
        for (size_t i = 1; i < dtype.size(); ++i) {
            if (dtype[i] < '0' || dtype[i] > '9' ||
                width > (static_cast<size_t>(std::numeric_limits<int>::max()) - (dtype[i] - '0')) / 10) {
                throw invalid_argument("string item size must be a positive int fitting a C int");
            }
            width = width * 10 + (dtype[i] - '0');
        }
        if (width == 0) throw invalid_argument("string item size must be a positive int");
        return width;
    }
    error("invalid type: " << dtype << " expected i1, i4, i8, f4, f8, M8[*] or S[n]");
}

template<typename T> void store_value(const char* text, unsigned char* destination) {
    const T value = parse_string<T>(text);
    ::memcpy(destination, &value, sizeof(T));
}

void add_line(const vector<char*>& fields, vector<CsvColumn>& data, size_t maximum) {
    for (size_t i = 0; i < data.size(); ++i) {
        CsvColumn& column = data[i];
        const size_t old_size = column.values.size();
        if (column.itemsize > maximum - old_size) error("CSV output byte limit exceeded");
        const size_t needed = old_size + column.itemsize;
        if (needed > column.values.capacity()) {
            const size_t capacity = column.values.capacity();
            const size_t grown = capacity > maximum / 2 ? maximum : capacity * 2;
            column.values.reserve(std::max(needed, grown));
        }
        column.values.resize(needed);
        unsigned char* destination = column.values.data() + old_size;
        const string& dtype = column.dtype;
        if (dtype[0] == 'S') {
            ::memset(destination, 0, column.itemsize);
            ::memcpy(destination, fields[i], std::min(column.itemsize, ::strlen(fields[i])));
        } else if (dtype == "f4") store_value<float>(fields[i], destination);
        else if (dtype == "f8") store_value<double>(fields[i], destination);
        else if (dtype == "i1") store_value<int8_t>(fields[i], destination);
        else if (dtype == "i4") store_value<int32_t>(fields[i], destination);
        else store_value<int64_t>(fields[i], destination);
    }
}

struct Reader {
    explicit Reader(size_t maximum): maximum(maximum), consumed(0) {}
    virtual ssize_t getline(char** line) = 0;
    virtual string filename() = 0;
    virtual ssize_t read_bytes(char* data, size_t length) = 0;
    ssize_t fread(char* data, size_t length) {
        const size_t remaining = maximum - consumed;
        const size_t request = remaining < length ? remaining + 1 : length;
        const ssize_t count = read_bytes(data, request);
        if (count > 0) {
            if (static_cast<size_t>(count) > remaining) error("CSV input byte limit exceeded: " << filename());
            consumed += static_cast<size_t>(count);
        }
        return count;
    }
    virtual ~Reader() {}
private:
    size_t maximum;
    size_t consumed;
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


struct ZipArchiveCloser { void operator()(zip_t* value) const { if (value) zip_discard(value); } };
struct ZipMemberCloser { void operator()(zip_file_t* value) const { if (value) zip_fclose(value); } };
struct ZipError {
    zip_error_t value;
    explicit ZipError(int code) { zip_error_init_with_code(&value, code); }
    ~ZipError() { zip_error_fini(&value); }
};

class ZipReader: public Reader {
public:
    ZipReader(const std::string& filename, size_t maximum):
    Reader(maximum),
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
        _zip_archive.reset(zip_open(zip_filename.c_str(), ZIP_RDONLY, &zip_error_code));
        if (!_zip_archive) {
            ZipError zip_error(zip_error_code);
            const string message = zip_error_strerror(&zip_error.value);
            error("can't read: " << zip_filename << " : " << message);
        }
        zip_stat_t member_stat;
        zip_stat_init(&member_stat);
        if (zip_stat(_zip_archive.get(), inner_filename.c_str(), ZIP_FL_ENC_GUESS, &member_stat) != 0) {
            const string message = zip_strerror(_zip_archive.get());
            error("can't inspect " << inner_filename << " from " << filename << " : " << message);
        }
        if ((member_stat.valid & ZIP_STAT_SIZE) && member_stat.size > MAX_ZIP_MEMBER_SIZE) {
            error(inner_filename << " from " << filename << " exceeds the 1 GiB decompressed member limit");
        }
        if ((member_stat.valid & ZIP_STAT_SIZE) && member_stat.size > maximum) {
            error(inner_filename << " exceeds the CSV input byte limit");
        }
        _zip_file.reset(zip_fopen(_zip_archive.get(), inner_filename.c_str(), ZIP_FL_ENC_GUESS));
        if (!_zip_file) {
            const string message = zip_strerror(_zip_archive.get());
            error("can't read " << inner_filename << " from " << filename << " : " << message);
        }
    }

    string filename() override { return _filename; }

    ssize_t getline(char** line) override {
        return read_line(&_buf, &_buf_size, &_buf_idx, line, this);
    }

    ssize_t read_bytes(char* buf, size_t buf_size) override {
        zip_int64_t bytes_read = zip_fread(_zip_file.get(), buf, buf_size);
        if (bytes_read < 0) error("error reading " << _filename << " : " << zip_file_strerror(_zip_file.get()));
        return static_cast<ssize_t>(bytes_read);
    }

    ~ZipReader() {
        if (_buf) ::free(_buf);
    }

private:
    string _filename;
    unique_ptr<zip_t, ZipArchiveCloser> _zip_archive;
    unique_ptr<zip_file_t, ZipMemberCloser> _zip_file;
    char* _buf;
    size_t _buf_idx;
    size_t _buf_size;
};

class FileReader: public Reader {
public:
    FileReader(const std::string& filename, size_t maximum):
        Reader(maximum),
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

    ssize_t read_bytes(char* buf, size_t buf_size) override {
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
                   vector<CsvColumn>& output,
                   const CsvLimits& limits) {

    size_t row_num = 0;
    size_t data_row_count = 0;
    size_t row_width = 0;
    for (size_t i = 0; i < dtypes.size(); ++i) {
        const size_t width = csv_itemsize(dtypes[i]);
        if (width > limits.max_output_bytes - row_width) error("CSV output byte limit exceeded by schema");
        row_width += width;
        output.emplace_back(dtypes[i], width);
    }

    bool more_to_read = true;
    for (;;) {
        if (max_rows != 0 && data_row_count >= static_cast<size_t>(max_rows)) break;
        char* line = nullptr;
        ssize_t line_size = reader->getline(&line);
        if (line_size <= 0) {
            //eof or error.  ::getline returns zero in both cases, zip_fread returns -1 for error, 0 for eof
            more_to_read = false;
            break;
        }
        // cout << "row num: " << row_num << " len: " << strlen(line) << " " << line << endl;
        row_num++;

        if (row_num <= static_cast<size_t>(skip_rows)) continue;
        auto fields = tokenize_line(line, separator, col_indices);
        if (!fields.size()) continue; // empty line
        if (fields.size() != dtypes.size()) {
            //replace nulls we added with separator so we can print out the line
            string _line(line, line_size);
            std::replace(_line.begin(), _line.end(), '\0', separator);
            error(reader->filename() << " found " << fields.size() << " " << " fields on row: " << row_num
                  << " line: " << _line << " but dtypes arg length was " << dtypes.size() << endl)
        }
        if (data_row_count >= limits.max_output_bytes / row_width) error("CSV output byte limit exceeded");
        add_line(fields, output, limits.max_output_bytes);
        data_row_count++;
    }
    return more_to_read;
}

bool read_csv(const std::string& filename,
              const std::vector<int>& col_indices,
              const std::vector<std::string>& dtypes,
              char separator,
              int skip_rows,
              int max_rows,
              std::vector<CsvColumn>& output,
              const CsvLimits& limits) {
    if (limits.max_input_bytes == 0 || limits.max_output_bytes == 0 || limits.max_columns == 0)
        throw invalid_argument("CSV limits must be positive");
    if (dtypes.empty() || dtypes.size() != col_indices.size() || dtypes.size() > limits.max_columns)
        throw invalid_argument("CSV schema size/column limit invalid");
    if (skip_rows < 0 || max_rows < 0) throw invalid_argument("CSV row counts must be nonnegative");
    int previous = -1;
    for (int index: col_indices) {
        if (index <= previous) throw invalid_argument("CSV column indices must be monotonically increasing");
        previous = index;
    }
    bool more_to_read = false;
    std::size_t i = filename.find(':');
    unique_ptr<Reader> reader;
    if (i == filename.npos) {
        reader.reset(new FileReader(filename, limits.max_input_bytes));
    } else {
        reader.reset(new ZipReader(filename, limits.max_input_bytes));
    }
    vector<CsvColumn> pending;
    more_to_read = read_csv_file(reader.get(), col_indices, dtypes, separator, skip_rows, max_rows, pending, limits);
    output.swap(pending);
    return more_to_read;
}
