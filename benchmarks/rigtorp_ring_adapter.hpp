#pragma once

// Benchmark-only adapter for rigtorp/SPSCQueue at
// 1053918dbd251fbff69b24ef27fa5d51c29ec2af. Build in an isolated source copy.
// Its public API exposes a single front record, not a contiguous batch lease.
#include "rigtorp_SPSCQueue.h"
#include <cstdint>
#include <stdexcept>
#include <vector>

namespace gambit {
template <typename Record>
class SpscRing {
public:
    struct ReadSpan { const Record* data; std::uint64_t count; };
    explicit SpscRing(std::uint64_t capacity) : queue_(checked_capacity(capacity)) {}
    bool try_push(const Record& record) { return queue_.try_push(record); }
    std::uint64_t push_batch(const Record* records, std::uint64_t maximum) {
        std::uint64_t count = 0;
        while (count < maximum && queue_.try_push(records[count])) ++count;
        return count;
    }
    bool try_pop(Record& record) {
        auto* front = queue_.front();
        if (!front) return false;
        record = *front;
        queue_.pop();
        return true;
    }
    template <typename Consumer>
    std::uint64_t consume(std::uint64_t maximum, Consumer consumer) {
        std::uint64_t count = 0;
        while (count < maximum) {
            auto* front = queue_.front();
            if (!front) break;
            consumer(*front);
            queue_.pop();
            ++count;
        }
        return count;
    }
    ReadSpan read_span(std::uint64_t maximum) const {
        auto* front = maximum ? queue_.front() : nullptr;
        return ReadSpan{front, front ? 1u : 0u};
    }
    void release(std::uint64_t count) {
        if (count > 1 || (count && !queue_.front())) {
            throw std::logic_error("ring release exceeds available records");
        }
        if (count) queue_.pop();
    }
    std::uint64_t capacity() const { return queue_.capacity(); }
    std::uint64_t depth() const { return queue_.size(); }
private:
    static std::uint64_t checked_capacity(std::uint64_t capacity) {
        if (capacity < 2 || (capacity & (capacity - 1)) != 0 ||
            capacity > std::vector<Record>().max_size()) {
            throw std::invalid_argument("ring capacity must be a supported power of two and at least two");
        }
        return capacity;
    }
    mutable rigtorp::SPSCQueue<Record> queue_;
};
}  // namespace gambit
