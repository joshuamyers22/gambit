#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>

#include <cerrno>
#include <cstdint>
#include <cstring>
#include <fcntl.h>
#include <stdexcept>
#include <string>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include <utility>

namespace py = pybind11;

namespace {

constexpr std::size_t HEADER_BYTES = 4096;
constexpr std::uint32_t FORMAT_VERSION = 1;
constexpr std::uint32_t STATE_COMMITTED = 1;
constexpr char MAGIC[8] = {'G', 'A', 'M', 'B', 'I', 'T', 'F', 'C'};

struct alignas(64) Header {
    char magic[8];
    std::uint32_t version;
    std::uint32_t state;
    std::uint64_t row_count;
    std::uint64_t data_offset;
    std::uint64_t data_bytes;
    std::uint64_t checksum;
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

class MappedFloat64Column {
public:
    static MappedFloat64Column* create(const std::string& path, py::array_t<double, py::array::c_style | py::array::forcecast> values) {
        const auto info = values.request();
        const auto rows = static_cast<std::uint64_t>(info.size);
        const auto data_bytes = static_cast<std::uint64_t>(info.size * sizeof(double));
        const auto mapping_bytes = HEADER_BYTES + data_bytes;
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
            header->version = FORMAT_VERSION;
            header->row_count = rows;
            header->data_offset = HEADER_BYTES;
            header->data_bytes = data_bytes;
            auto* destination = static_cast<unsigned char*>(mapping) + HEADER_BYTES;
            std::memcpy(destination, info.ptr, data_bytes);
            header->checksum = checksum_bytes(destination, data_bytes);

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
                throw error;
            }
            if (::mprotect(mapping, mapping_bytes, PROT_READ) == -1) {
                const auto error = system_error("protect factor cache segment");
                ::munmap(mapping, mapping_bytes);
                ::close(fd);
                throw error;
            }
        }
        return new MappedFloat64Column(path, fd, mapping, mapping_bytes);
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
        return new MappedFloat64Column(path, fd, mapping, mapping_bytes);
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
        auto* data = static_cast<unsigned char*>(mapping_) + header->data_offset;
        py::array array(
            py::dtype::of<double>(),
            {static_cast<py::ssize_t>(header->row_count)},
            {static_cast<py::ssize_t>(sizeof(double))},
            data,
            py::cast(this, py::return_value_policy::reference)
        );
        array.attr("setflags")(false);
        return array;
    }

    std::uint64_t row_count() const { return static_cast<const Header*>(mapping_)->row_count; }
    std::uint64_t checksum() const { return static_cast<const Header*>(mapping_)->checksum; }
    const std::string& path() const { return path_; }

private:
    MappedFloat64Column(std::string path, int fd, void* mapping, std::size_t mapping_bytes)
        : path_(std::move(path)), fd_(fd), mapping_(mapping), mapping_bytes_(mapping_bytes) {}

    static void validate(const void* mapping, std::size_t mapping_bytes) {
        const auto* header = static_cast<const Header*>(mapping);
        if (std::memcmp(header->magic, MAGIC, sizeof(MAGIC)) != 0 || header->version != FORMAT_VERSION) {
            throw std::runtime_error("factor cache header is invalid or unsupported");
        }
        if (__atomic_load_n(&header->state, __ATOMIC_ACQUIRE) != STATE_COMMITTED) {
            throw std::runtime_error("factor cache segment is not committed");
        }
        if (header->data_offset != HEADER_BYTES || header->data_bytes != header->row_count * sizeof(double) ||
            header->data_offset + header->data_bytes != mapping_bytes) {
            throw std::runtime_error("factor cache segment bounds are invalid");
        }
        const auto* data = static_cast<const unsigned char*>(mapping) + header->data_offset;
        if (checksum_bytes(data, header->data_bytes) != header->checksum) {
            throw std::runtime_error("factor cache checksum mismatch");
        }
    }

    std::string path_;
    int fd_;
    void* mapping_;
    std::size_t mapping_bytes_;
};

}  // namespace

PYBIND11_MODULE(_factor_cache, module) {
    py::class_<MappedFloat64Column>(module, "MappedFloat64Column")
        .def_static("create", &MappedFloat64Column::create, py::arg("path"), py::arg("values"))
        .def_static("open", &MappedFloat64Column::open_existing, py::arg("path"))
        .def_property_readonly("values", &MappedFloat64Column::values)
        .def_property_readonly("row_count", &MappedFloat64Column::row_count)
        .def_property_readonly("checksum", &MappedFloat64Column::checksum)
        .def_property_readonly("path", &MappedFloat64Column::path);
}
