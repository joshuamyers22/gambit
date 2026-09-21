// Benchmark-only, integer-identical implementation of make_queue_events(rate=10).
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>

namespace {
struct Record {
    std::uint64_t sequence;
    std::int64_t event_time_ns, receive_time_ns, bid, ask, bid_size, ask_size;
    std::uint32_t instrument_id, flags;
    std::int64_t trade_price, trade_size;
    std::int32_t aggressor;
    std::uint32_t reserved;
};
static_assert(sizeof(Record) == 88 && offsetof(Record, instrument_id) == 56 &&
              offsetof(Record, trade_price) == 64 && offsetof(Record, aggressor) == 80,
              "QUEUE_DTYPE layout mismatch");
}

extern "C" int gambit_fill_queue(void* destination, std::uint64_t bytes,
                                 std::uint64_t offset, std::uint64_t count, std::uint64_t seed) {
    constexpr std::uint64_t period = 100000000;
    constexpr std::uint64_t last = (std::numeric_limits<std::int64_t>::max() - 1000000) / period;
    if (count > 1048576 || bytes < count * sizeof(Record) || (!destination && count) ||
        offset > last || (count && count - 1 > last - offset)) return -1;
    const std::int64_t bases[] = {300000000, 20000000, 300000, 5000, 3000000, 1000, 100000, 1000000};
    auto* output = static_cast<unsigned char*>(destination);
    for (std::uint64_t index = 0; index < count; ++index) {
        const auto sequence = offset + index;
        auto mixed = sequence + seed; // Unsigned wrap matches NumPy uint64 arithmetic.
        mixed = (mixed ^ (mixed >> 30)) * UINT64_C(0xBF58476D1CE4E5B9);
        mixed = (mixed ^ (mixed >> 27)) * UINT64_C(0x94D049BB133111EB);
        mixed ^= mixed >> 31;
        Record record{};
        record.sequence = sequence;
        record.event_time_ns = static_cast<std::int64_t>(sequence * period);
        record.receive_time_ns = record.event_time_ns + 1000000;
        record.instrument_id = static_cast<std::uint32_t>(sequence % 8);
        record.bid = bases[record.instrument_id] + static_cast<std::int64_t>(sequence / 2048 % 31);
        record.ask = record.bid + 2;
        record.bid_size = 50 + static_cast<std::int64_t>(mixed % 101);
        record.ask_size = 50 + static_cast<std::int64_t>((mixed >> 8) % 101);
        record.aggressor = mixed % 2 == 0 ? -1 : 1;
        record.trade_price = record.aggressor == -1 ? record.bid : record.ask;
        record.trade_size = 1 + static_cast<std::int64_t>((mixed >> 16) % 31);
        std::memcpy(output + index * sizeof(Record), &record, sizeof(Record));
    }
    return 0;
}
