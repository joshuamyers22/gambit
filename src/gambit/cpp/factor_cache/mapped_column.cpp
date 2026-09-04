#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>

#include <cerrno>
#include <algorithm>
#include <cstdint>
#include <cstring>
#include <fcntl.h>
#include <limits>
#include <mutex>
#include <stdexcept>
#include <string>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include <utility>
#include <vector>

namespace py = pybind11;

void init_tick_ring(py::module_& module);
void init_top_of_book_backtest(py::module_& module);

namespace {

constexpr std::size_t HEADER_BYTES = 4096;
constexpr std::uint32_t FORMAT_VERSION_V1 = 1;
constexpr std::uint32_t FORMAT_VERSION_V2 = 2;
constexpr std::uint32_t FORMAT_VERSION_V3 = 3;
constexpr std::uint32_t STATE_COMMITTED = 1;
constexpr std::uint64_t DEFAULT_CHUNK_BYTES = 256 * 1024;
constexpr char MAGIC[8] = {'G', 'A', 'M', 'B', 'I', 'T', 'F', 'C'};

struct alignas(64) Header {
    char magic[8];
    std::uint32_t version;
    std::uint32_t state;
    std::uint64_t row_count;
    std::uint64_t data_offset;
    std::uint64_t data_bytes;
    std::uint64_t checksum;
    std::uint64_t chunk_bytes;
    std::uint64_t chunk_count;
};

static_assert(sizeof(Header) <= HEADER_BYTES, "factor cache header exceeds reserved page");

std::runtime_error system_error(const std::string& operation) {
    return std::runtime_error(operation + ": " + std::strerror(errno));
}

std::uint64_t checksum_bytes(const unsigned char* data, std::size_t size) {
    std::uint64_t hash = 1469598103934665603ULL;
    for (std::size_t i = 0; i < size; ++i) {
        hash ^= data[i];
        hash *= 1099511628211ULL;
    }
    return hash;
}

std::uint64_t rotate_left(std::uint64_t value, unsigned int bits) {
    return (value << bits) | (value >> (64U - bits));
}

std::uint64_t read_u64_le(const unsigned char* data) {
    return static_cast<std::uint64_t>(data[0]) |
        (static_cast<std::uint64_t>(data[1]) << 8U) |
        (static_cast<std::uint64_t>(data[2]) << 16U) |
        (static_cast<std::uint64_t>(data[3]) << 24U) |
        (static_cast<std::uint64_t>(data[4]) << 32U) |
        (static_cast<std::uint64_t>(data[5]) << 40U) |
        (static_cast<std::uint64_t>(data[6]) << 48U) |
        (static_cast<std::uint64_t>(data[7]) << 56U);
}

std::uint32_t read_u32_le(const unsigned char* data) {
    return static_cast<std::uint32_t>(data[0]) |
        (static_cast<std::uint32_t>(data[1]) << 8U) |
        (static_cast<std::uint32_t>(data[2]) << 16U) |
        (static_cast<std::uint32_t>(data[3]) << 24U);
}

std::uint64_t xxh64_round(std::uint64_t accumulator, std::uint64_t input) {
    constexpr std::uint64_t prime1 = 11400714785074694791ULL;
    constexpr std::uint64_t prime2 = 14029467366897019727ULL;
    accumulator += input * prime2;
    accumulator = rotate_left(accumulator, 31U);
    return accumulator * prime1;
}

std::uint64_t xxh64_merge(std::uint64_t accumulator, std::uint64_t value) {
    constexpr std::uint64_t prime1 = 11400714785074694791ULL;
    constexpr std::uint64_t prime4 = 9650029242287828579ULL;
    accumulator ^= xxh64_round(0, value);
    return accumulator * prime1 + prime4;
}

std::uint64_t checksum_bytes_fast(const unsigned char* data, std::size_t size) {
    constexpr std::uint64_t prime1 = 11400714785074694791ULL;
    constexpr std::uint64_t prime2 = 14029467366897019727ULL;
    constexpr std::uint64_t prime3 = 1609587929392839161ULL;
    constexpr std::uint64_t prime4 = 9650029242287828579ULL;
    constexpr std::uint64_t prime5 = 2870177450012600261ULL;
    const auto* cursor = data;
    const auto* end = data + size;
    std::uint64_t hash;
    if (size >= 32) {
        std::uint64_t v1 = prime1 + prime2;
        std::uint64_t v2 = prime2;
        std::uint64_t v3 = 0;
        std::uint64_t v4 = 0 - prime1;
        const auto* limit = end - 32;
        do {
            v1 = xxh64_round(v1, read_u64_le(cursor));
            cursor += 8;
            v2 = xxh64_round(v2, read_u64_le(cursor));
            cursor += 8;
            v3 = xxh64_round(v3, read_u64_le(cursor));
            cursor += 8;
            v4 = xxh64_round(v4, read_u64_le(cursor));
            cursor += 8;
        } while (cursor <= limit);
        hash = rotate_left(v1, 1U) + rotate_left(v2, 7U) +
            rotate_left(v3, 12U) + rotate_left(v4, 18U);
        hash = xxh64_merge(hash, v1);
        hash = xxh64_merge(hash, v2);
        hash = xxh64_merge(hash, v3);
        hash = xxh64_merge(hash, v4);
    } else {
        hash = prime5;
    }
    hash += static_cast<std::uint64_t>(size);
    while (static_cast<std::size_t>(end - cursor) >= 8) {
        const auto value = xxh64_round(0, read_u64_le(cursor));
        hash ^= value;
        hash = rotate_left(hash, 27U) * prime1 + prime4;
        cursor += 8;
    }
    if (static_cast<std::size_t>(end - cursor) >= 4) {
        hash ^= static_cast<std::uint64_t>(read_u32_le(cursor)) * prime1;
        hash = rotate_left(hash, 23U) * prime2 + prime3;
        cursor += 4;
    }
    while (cursor < end) {
        hash ^= static_cast<std::uint64_t>(*cursor) * prime5;
        hash = rotate_left(hash, 11U) * prime1;
        ++cursor;
    }
    hash ^= hash >> 33U;
    hash *= prime2;
    hash ^= hash >> 29U;
    hash *= prime3;
    hash ^= hash >> 32U;
    return hash;
}

std::uint64_t checksum_for_version(
    std::uint32_t version,
    const unsigned char* data,
    std::size_t size
) {
    return version == FORMAT_VERSION_V3 ? checksum_bytes_fast(data, size) : checksum_bytes(data, size);
}

class MappedFloat64Column {
public:
    static MappedFloat64Column* create(const std::string& path, py::array_t<double, py::array::c_style | py::array::forcecast> values) {
        return create_impl(path, values, FORMAT_VERSION_V1);
    }

    static MappedFloat64Column* create_chunked(
        const std::string& path,
        py::array_t<double, py::array::c_style | py::array::forcecast> values
    ) {
        return create_impl(path, values, FORMAT_VERSION_V2);
    }

    static MappedFloat64Column* create_chunked_v3(
        const std::string& path,
        py::array_t<double, py::array::c_style | py::array::forcecast> values
    ) {
        return create_impl(path, values, FORMAT_VERSION_V3);
    }

    static MappedFloat64Column* create_impl(
        const std::string& path,
        py::array_t<double, py::array::c_style | py::array::forcecast> values,
        std::uint32_t format_version
    ) {
        const auto info = values.request();
        if (info.size < 0 || static_cast<std::uint64_t>(info.size) >
                std::numeric_limits<std::uint64_t>::max() / sizeof(double)) {
            throw std::overflow_error("factor cache row count is too large");
        }
        const auto rows = static_cast<std::uint64_t>(info.size);
        const auto data_bytes = rows * sizeof(double);
        if (data_bytes > std::numeric_limits<std::size_t>::max() - HEADER_BYTES ||
            data_bytes > static_cast<std::uint64_t>(std::numeric_limits<off_t>::max()) - HEADER_BYTES) {
            throw std::overflow_error("factor cache mapping size is too large");
        }
        const auto mapping_bytes = static_cast<std::size_t>(HEADER_BYTES + data_bytes);
        int fd = ::open(path.c_str(), O_RDWR | O_CREAT | O_EXCL, 0600);
        if (fd == -1) {
            throw system_error("open factor cache segment");
        }
        if (::ftruncate(fd, static_cast<off_t>(mapping_bytes)) == -1) {
            const auto error = system_error("resize factor cache segment");
            ::close(fd);
            ::unlink(path.c_str());
            throw error;
        }
        void* mapping = ::mmap(nullptr, mapping_bytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
        if (mapping == MAP_FAILED) {
            const auto error = system_error("map factor cache segment");
            ::close(fd);
            ::unlink(path.c_str());
            throw error;
        }

        {
            py::gil_scoped_release release;
            auto* header = static_cast<Header*>(mapping);
            std::memset(mapping, 0, HEADER_BYTES);
            std::memcpy(header->magic, MAGIC, sizeof(MAGIC));
            header->version = format_version;
            header->row_count = rows;
            header->data_offset = HEADER_BYTES;
            header->data_bytes = data_bytes;
            auto* destination = static_cast<unsigned char*>(mapping) + HEADER_BYTES;
            std::memcpy(destination, info.ptr, data_bytes);
            header->checksum = checksum_for_version(format_version, destination, data_bytes);
            if (format_version == FORMAT_VERSION_V2 || format_version == FORMAT_VERSION_V3) {
                const auto maximum_chunks = (HEADER_BYTES - sizeof(Header)) / sizeof(std::uint64_t);
                const auto minimum_chunk_bytes = data_bytes == 0 ? 1 : 1 + (data_bytes - 1) / maximum_chunks;
                header->chunk_bytes = std::max(DEFAULT_CHUNK_BYTES, minimum_chunk_bytes);
                header->chunk_count = data_bytes == 0 ? 0 : (data_bytes + header->chunk_bytes - 1) / header->chunk_bytes;
                auto* chunk_checksums = reinterpret_cast<std::uint64_t*>(
                    static_cast<unsigned char*>(mapping) + sizeof(Header)
                );
                for (std::uint64_t chunk = 0; chunk < header->chunk_count; ++chunk) {
                    const auto offset = chunk * header->chunk_bytes;
                    const auto length = std::min(header->chunk_bytes, data_bytes - offset);
                    chunk_checksums[chunk] = checksum_for_version(
                        format_version, destination + offset, static_cast<std::size_t>(length)
                    );
                }
            }

            if (::msync(mapping, mapping_bytes, MS_SYNC) == -1) {
                const auto error = system_error("flush factor cache data");
                ::munmap(mapping, mapping_bytes);
                ::close(fd);
                ::unlink(path.c_str());
                throw error;
            }
            __atomic_store_n(&header->state, STATE_COMMITTED, __ATOMIC_RELEASE);
            if (::msync(mapping, HEADER_BYTES, MS_SYNC) == -1) {
                const auto error = system_error("publish factor cache header");
                ::munmap(mapping, mapping_bytes);
                ::close(fd);
                ::unlink(path.c_str());
                throw error;
            }
            if (::mprotect(mapping, mapping_bytes, PROT_READ) == -1) {
                const auto error = system_error("protect factor cache segment");
                ::munmap(mapping, mapping_bytes);
                ::close(fd);
                ::unlink(path.c_str());
                throw error;
            }
        }
        return new MappedFloat64Column(path, fd, mapping, mapping_bytes, true);
    }

    static MappedFloat64Column* open_existing(const std::string& path) {
        int fd = ::open(path.c_str(), O_RDONLY);
        if (fd == -1) {
            throw system_error("open factor cache segment");
        }
        struct stat status {};
        if (::fstat(fd, &status) == -1 || status.st_size < static_cast<off_t>(HEADER_BYTES)) {
            ::close(fd);
            throw std::runtime_error("factor cache segment is truncated");
        }
        const auto mapping_bytes = static_cast<std::size_t>(status.st_size);
        void* mapping = ::mmap(nullptr, mapping_bytes, PROT_READ, MAP_SHARED, fd, 0);
        if (mapping == MAP_FAILED) {
            const auto error = system_error("map factor cache segment");
            ::close(fd);
            throw error;
        }
        try {
            py::gil_scoped_release release;
            validate(mapping, mapping_bytes);
        } catch (...) {
            ::munmap(mapping, mapping_bytes);
            ::close(fd);
            throw;
        }
        return new MappedFloat64Column(path, fd, mapping, mapping_bytes, false);
    }

    MappedFloat64Column(const MappedFloat64Column&) = delete;
    MappedFloat64Column& operator=(const MappedFloat64Column&) = delete;

    ~MappedFloat64Column() {
        if (mapping_ != MAP_FAILED) {
            ::munmap(mapping_, mapping_bytes_);
        }
        if (fd_ != -1) {
            ::close(fd_);
        }
    }

    py::array values() {
        const auto* header = static_cast<const Header*>(mapping_);
        {
            py::gil_scoped_release release;
            verify_range(0, header->row_count);
        }
        return array_view(0, header->row_count);
    }

    py::array slice(std::uint64_t start, std::uint64_t stop) {
        const auto* header = static_cast<const Header*>(mapping_);
        if (start > stop || stop > header->row_count) {
            throw std::out_of_range("factor cache slice bounds are invalid");
        }
        {
            py::gil_scoped_release release;
            verify_range(start, stop);
        }
        return array_view(start, stop);
    }

    py::array array_view(std::uint64_t start, std::uint64_t stop) {
        const auto* header = static_cast<const Header*>(mapping_);
        if (stop - start > static_cast<std::uint64_t>(std::numeric_limits<py::ssize_t>::max())) {
            throw std::overflow_error("factor cache view is too large for NumPy");
        }
        auto* data = static_cast<unsigned char*>(mapping_) + header->data_offset;
        py::array array(
            py::dtype::of<double>(),
            {static_cast<py::ssize_t>(stop - start)},
            {static_cast<py::ssize_t>(sizeof(double))},
            data + start * sizeof(double),
            py::cast(this, py::return_value_policy::reference)
        );
        array.attr("setflags")(false);
        return array;
    }

    std::uint64_t row_count() const { return static_cast<const Header*>(mapping_)->row_count; }
    std::uint64_t checksum() const { return static_cast<const Header*>(mapping_)->checksum; }
    std::uint32_t format_version() const { return static_cast<const Header*>(mapping_)->version; }
    std::uint64_t verified_chunks() const {
        std::lock_guard<std::mutex> lock(verification_mutex_);
        return static_cast<std::uint64_t>(std::count(verified_.begin(), verified_.end(), 1));
    }
    const std::string& path() const { return path_; }

private:
    MappedFloat64Column(std::string path, int fd, void* mapping, std::size_t mapping_bytes, bool created)
        : path_(std::move(path)), fd_(fd), mapping_(mapping), mapping_bytes_(mapping_bytes) {
        const auto* header = static_cast<const Header*>(mapping_);
        const auto count = header->version == FORMAT_VERSION_V1 ? 1 : header->chunk_count;
        verified_.assign(static_cast<std::size_t>(count), created || header->version == FORMAT_VERSION_V1 ? 1 : 0);
    }

    static void validate(const void* mapping, std::size_t mapping_bytes) {
        const auto* header = static_cast<const Header*>(mapping);
        if (std::memcmp(header->magic, MAGIC, sizeof(MAGIC)) != 0 ||
            (header->version != FORMAT_VERSION_V1 && header->version != FORMAT_VERSION_V2 &&
             header->version != FORMAT_VERSION_V3)) {
            throw std::runtime_error("factor cache header is invalid or unsupported");
        }
        if (__atomic_load_n(&header->state, __ATOMIC_ACQUIRE) != STATE_COMMITTED) {
            throw std::runtime_error("factor cache segment is not committed");
        }
        if (header->row_count > std::numeric_limits<std::uint64_t>::max() / sizeof(double) ||
            header->data_offset != HEADER_BYTES || header->data_bytes != header->row_count * sizeof(double) ||
            header->data_bytes > mapping_bytes - HEADER_BYTES ||
            header->data_offset + header->data_bytes != mapping_bytes) {
            throw std::runtime_error("factor cache segment bounds are invalid");
        }
        const auto* data = static_cast<const unsigned char*>(mapping) + header->data_offset;
        if (header->version == FORMAT_VERSION_V1) {
            if (checksum_bytes(data, header->data_bytes) != header->checksum) {
                throw std::runtime_error("factor cache checksum mismatch");
            }
        } else {
            const auto maximum_chunks = (HEADER_BYTES - sizeof(Header)) / sizeof(std::uint64_t);
            if (header->chunk_bytes == 0) {
                throw std::runtime_error("factor cache chunk table is invalid");
            }
            const auto expected_chunks = header->data_bytes == 0 ? 0 :
                1 + (header->data_bytes - 1) / header->chunk_bytes;
            if (header->chunk_count != expected_chunks ||
                header->chunk_count > maximum_chunks) {
                throw std::runtime_error("factor cache chunk table is invalid");
            }
        }
    }

    void verify_range(std::uint64_t start, std::uint64_t stop) {
        const auto* header = static_cast<const Header*>(mapping_);
        if (header->version == FORMAT_VERSION_V1 || start == stop) {
            return;
        }
        const auto first_byte = start * sizeof(double);
        const auto stop_byte = stop * sizeof(double);
        const auto first_chunk = first_byte / header->chunk_bytes;
        const auto last_chunk = (stop_byte - 1) / header->chunk_bytes;
        const auto* data = static_cast<const unsigned char*>(mapping_) + header->data_offset;
        const auto* chunk_checksums = reinterpret_cast<const std::uint64_t*>(
            static_cast<const unsigned char*>(mapping_) + sizeof(Header)
        );
        std::lock_guard<std::mutex> lock(verification_mutex_);
        for (auto chunk = first_chunk; chunk <= last_chunk; ++chunk) {
            if (verified_[static_cast<std::size_t>(chunk)] != 0) {
                continue;
            }
            const auto offset = chunk * header->chunk_bytes;
            const auto length = std::min(header->chunk_bytes, header->data_bytes - offset);
            if (checksum_for_version(
                    header->version, data + offset, static_cast<std::size_t>(length)
                ) != chunk_checksums[chunk]) {
                throw std::runtime_error("factor cache chunk checksum mismatch");
            }
            verified_[static_cast<std::size_t>(chunk)] = 1;
        }
    }

    std::string path_;
    int fd_;
    void* mapping_;
    std::size_t mapping_bytes_;
    mutable std::mutex verification_mutex_;
    std::vector<unsigned char> verified_;
};

}  // namespace

PYBIND11_MODULE(_factor_cache, module) {
    py::class_<MappedFloat64Column>(module, "MappedFloat64Column")
        .def_static("create", &MappedFloat64Column::create, py::arg("path"), py::arg("values"))
        .def_static("create_chunked", &MappedFloat64Column::create_chunked, py::arg("path"), py::arg("values"))
        .def_static("create_chunked_v3", &MappedFloat64Column::create_chunked_v3, py::arg("path"), py::arg("values"))
        .def_static("open", &MappedFloat64Column::open_existing, py::arg("path"))
        .def_property_readonly("values", &MappedFloat64Column::values)
        .def_property_readonly("row_count", &MappedFloat64Column::row_count)
        .def_property_readonly("checksum", &MappedFloat64Column::checksum)
        .def_property_readonly("format_version", &MappedFloat64Column::format_version)
        .def_property_readonly("verified_chunks", &MappedFloat64Column::verified_chunks)
        .def("slice", &MappedFloat64Column::slice, py::arg("start"), py::arg("stop"))
        .def_property_readonly("path", &MappedFloat64Column::path);
    init_tick_ring(module);
    init_top_of_book_backtest(module);
}
