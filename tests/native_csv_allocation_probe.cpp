// Standalone fault injection: no production test switches or Python allocator changes.
#include <cstdio>
#include <cstdlib>
#include <new>
#include <stdexcept>
#include <string>
#include <vector>
#include "csv_reader.hpp"

static size_t live_allocations = 0;
static size_t remaining = 0;
static bool failing = false;

void* operator new(size_t size) {
    if (failing && remaining-- == 0) throw std::bad_alloc();
    void* value = std::malloc(size ? size : 1);
    if (!value) throw std::bad_alloc();
    ++live_allocations;
    return value;
}
void operator delete(void* value) noexcept {
    if (value) { --live_allocations; std::free(value); }
}
void* operator new[](size_t size) { return ::operator new(size); }
void operator delete[](void* value) noexcept { ::operator delete(value); }

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    const std::string source(argv[1]);
    const std::vector<int> indices{0, 1};
    const std::vector<std::string> dtypes{"S3", "i8"};
    const size_t baseline = live_allocations;
    bool succeeded = false;
    size_t failures = 0;
    for (size_t fail_after = 0; fail_after < 1000 && !succeeded; ++fail_after) {
        remaining = fail_after;
        failing = true;
        try {
            std::vector<CsvColumn> output;
            read_csv(source, indices, dtypes, ',', 0, 0, output);
            failing = false;
            if (output.size() != 2 || output[0].values.size() != 9 || output[1].values.size() != 24) return 3;
            succeeded = true;
        } catch (const std::bad_alloc&) {
            failing = false;
            ++failures;
        } catch (...) {
            failing = false;
            return 4;
        }
        if (live_allocations != baseline) {
            std::fprintf(stderr, "C++ allocation leak after failure point %zu: %zu vs %zu\n",
                         fail_after, live_allocations, baseline);
            return 5;
        }
    }
    if (!succeeded || failures < 5) return 6;
    // A failed read must leave an existing caller-owned result untouched.
    {
        std::vector<CsvColumn> output;
        read_csv(source, indices, dtypes, ',', 0, 0, output);
        const auto before = output[0].values;
        CsvLimits limits;
        limits.max_output_bytes = 11;
        try {
            read_csv(source, indices, dtypes, ',', 0, 0, output, limits);
            return 7;
        } catch (const std::runtime_error&) {
            if (output[0].values != before) return 8;
        }
    }
    if (live_allocations != baseline) return 9;
    std::printf("injected %zu allocation failures; no retained C++ allocations\n", failures);
    return 0;
}
