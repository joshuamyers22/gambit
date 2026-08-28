#include "spsc_ring.hpp"

#include <atomic>
#include <cassert>
#include <cstdint>
#include <thread>

struct Record {
    std::uint64_t sequence;
};

int main() {
    constexpr std::uint64_t record_count = 1000000;
    gambit::SpscRing<Record> ring(1024);
    std::atomic<bool> failed{false};

    std::thread producer([&ring] {
        for (std::uint64_t sequence = 0; sequence < record_count; ++sequence) {
            const Record record{sequence};
            while (!ring.try_push(record)) {
                std::this_thread::yield();
            }
        }
    });
    std::thread consumer([&ring, &failed] {
        for (std::uint64_t expected = 0; expected < record_count; ++expected) {
            Record record{};
            while (!ring.try_pop(record)) {
                std::this_thread::yield();
            }
            if (record.sequence != expected) {
                failed.store(true, std::memory_order_relaxed);
            }
        }
    });

    producer.join();
    consumer.join();
    assert(!failed.load(std::memory_order_relaxed));
    assert(ring.depth() == 0);
}
