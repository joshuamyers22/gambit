// Replay the same target under ASan/UBSan where libFuzzer is unavailable.
// This driver provides no coverage-guided mutation.
#include <cstdint>
#include <fstream>
#include <iterator>
#include <vector>

extern "C" int LLVMFuzzerTestOneInput(const uint8_t*, size_t);

int main(int argc, char** argv) {
    for (int index = 1; index < argc; ++index) {
        std::ifstream file(argv[index], std::ios::binary);
        if (!file) return 2;
        std::vector<uint8_t> bytes((std::istreambuf_iterator<char>(file)), {});
        if (bytes.size() > 65536) return 2;
        if (LLVMFuzzerTestOneInput(bytes.data(), bytes.size()) != 0) return 1;
    }
    return 0;
}
