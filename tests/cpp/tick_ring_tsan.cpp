#include "spsc_ring.hpp"

#include <atomic>
#include <cassert>
#include <cstdint>
#include <thread>

struct Record {
    std::uint64_t sequence;
    std::uint64_t payload[7];
};

Record make_record(std::uint64_t sequence) {
    Record record{};
    record.sequence = sequence;
    for (std::uint64_t i = 0; i < 7; ++i) record.payload[i] = sequence ^ (i + 123);
    return record;
}

void exercise(std::uint64_t capacity, int mode) {
    constexpr std::uint64_t record_count = 200000;
    gambit::SpscRing<Record> ring(capacity);
    std::atomic<bool> failed{false};

    std::thread producer([&ring] {
        std::uint64_t sequence = 0;
        while (sequence < record_count) {
            if (ring.try_push(make_record(sequence))) {
                ++sequence;
            } else {
                std::this_thread::yield();
            }
        }
    });
    std::thread consumer([&ring, &failed, mode] {
        std::uint64_t expected = 0;
        const auto verify = [&expected, &failed](const Record& record) {
            for (std::uint64_t i = 0; i < 7; ++i) {
                if (record.payload[i] != (expected ^ (i + 123))) failed.store(true, std::memory_order_relaxed);
            }
            if (record.sequence != expected++) {
                failed.store(true, std::memory_order_relaxed);
            }
        };
        while (expected < record_count) {
            if (mode == 0) {
                Record record{};
                if (ring.try_pop(record)) verify(record);
                else std::this_thread::yield();
            } else if (mode == 1) {
                if (!ring.consume(17, verify)) std::this_thread::yield();
            } else {
                const auto span = ring.read_span(17);
                // Keep the borrowed storage pinned while the producer wraps.
                std::this_thread::yield();
                for (std::uint64_t i = 0; i < span.count; ++i) verify(span.data[i]);
                ring.release(span.count);
            }
        }
    });

    producer.join();
    consumer.join();
    assert(!failed.load(std::memory_order_relaxed));
    assert(ring.depth() == 0);
}

int main() {
    static_assert(sizeof(Record) == 64, "probe uses the same record width as TickRecord");
    for (auto capacity : {2u, 16u, 1024u}) {
        for (int mode = 0; mode < 3; ++mode) {
            exercise(capacity, mode);
        }
    }
}
