#pragma once

#include <atomic>
#include <cstdint>
#include <stdexcept>
#include <vector>

namespace gambit {

template <typename Record>
class SpscRing {
public:
    explicit SpscRing(std::uint64_t capacity)
        : capacity_(checked_capacity(capacity)), mask_(capacity_ - 1), slots_(capacity_) {}

    bool try_push(const Record& record) {
        const auto head = head_.value.load(std::memory_order_relaxed);
        const auto tail = tail_.value.load(std::memory_order_acquire);
        if (head - tail == capacity_) {
            return false;
        }
        slots_[head & mask_] = record;
        head_.value.store(head + 1, std::memory_order_release);
        return true;
    }

    bool try_pop(Record& record) {
        const auto tail = tail_.value.load(std::memory_order_relaxed);
        const auto head = head_.value.load(std::memory_order_acquire);
        if (tail == head) {
            return false;
        }
        record = slots_[tail & mask_];
        tail_.value.store(tail + 1, std::memory_order_release);
        return true;
    }

    template <typename Consumer>
    std::uint64_t consume(std::uint64_t maximum, Consumer consumer) {
        const auto tail = tail_.value.load(std::memory_order_relaxed);
        const auto head = head_.value.load(std::memory_order_acquire);
        const auto available = head - tail;
        const auto count = available < maximum ? available : maximum;
        for (std::uint64_t index = 0; index < count; ++index) {
            consumer(slots_[(tail + index) & mask_]);
        }
        tail_.value.store(tail + count, std::memory_order_release);
        return count;
    }

    std::uint64_t capacity() const { return capacity_; }

    std::uint64_t depth() const {
        const auto head = head_.value.load(std::memory_order_acquire);
        const auto tail = tail_.value.load(std::memory_order_acquire);
        return head - tail;
    }

private:
    static std::uint64_t checked_capacity(std::uint64_t capacity) {
        if (capacity < 2 || (capacity & (capacity - 1)) != 0 ||
            capacity > std::vector<Record>().max_size()) {
            throw std::invalid_argument("ring capacity must be a supported power of two and at least two");
        }
        return capacity;
    }

    struct alignas(64) Cursor {
        std::atomic<std::uint64_t> value{0};
    };

    const std::uint64_t capacity_;
    const std::uint64_t mask_;
    std::vector<Record> slots_;
    Cursor head_;
    Cursor tail_;
};

}  // namespace gambit
