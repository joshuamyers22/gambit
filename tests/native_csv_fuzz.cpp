// Standalone libFuzzer entry point; never linked into the Python extension.
#include "csv_reader.hpp"
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <stdexcept>
#include <string>
#include <vector>

extern "C" int LLVMFuzzerTestOneInput(const uint8_t* data, size_t size) {
    if (size > 65536) return 0;
    const char* path = std::getenv("GAMBIT_FUZZ_INPUT");
    const char* format = std::getenv("GAMBIT_FUZZ_FORMAT");
    if (!path || !format) std::abort();
    FILE* file = std::fopen(path, "wb");
    if (!file) std::abort();
    const size_t written = std::fwrite(data, 1, size, file);
    const int closed = std::fclose(file);
    if (written != size || closed != 0) std::abort();
    const std::string source = std::string(path) +
        (std::string(format) == "zip" ? ":data.csv" : "");
    CsvLimits limits;
    limits.max_input_bytes = 65536;
    limits.max_output_bytes = 4096;
    limits.max_columns = 2;
    for (const auto& dtype : {"i4", "i8", "i1", "f4", "f8", "S16"}) {
        std::vector<CsvColumn> output;
        try {
            read_csv(source, {0}, {dtype}, ',', 0, 256, output, limits);
        } catch (const std::runtime_error&) {
            continue;  // Malformed input and budget rejections are expected.
        }
        if (output.size() != 1 || output[0].values.size() > limits.max_output_bytes ||
            output[0].values.size() % output[0].itemsize != 0 ||
            output[0].values.size() / output[0].itemsize > 256) std::abort();
    }
    return 0;
}
