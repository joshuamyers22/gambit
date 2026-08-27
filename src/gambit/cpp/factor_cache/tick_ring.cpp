#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cmath>
#include <limits>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <unordered_map>
#include <vector>

namespace py = pybind11;

struct TickRecord {
    std::uint64_t sequence;
    std::int64_t event_time_ns;
    std::int64_t receive_time_ns;
    double price;
    double quantity;
    double bid;
    double ask;
    std::uint32_t instrument_id;
    std::uint32_t flags;
};

static_assert(sizeof(TickRecord) == 64, "TickRecord must occupy one cache line");

class TickFactorProcessor {
public:
    void process(const TickRecord& record) {
        if (has_expected_sequence_ && record.sequence != expected_sequence_) {
            ++sequence_errors_;
        }
        expected_sequence_ = record.sequence + 1;
        has_expected_sequence_ = true;
        ++processed_;
        total_quantity_ += record.quantity;
        total_notional_ += record.price * record.quantity;
        spread_sum_ += record.ask - record.bid;
        mid_sum_ += (record.ask + record.bid) * 0.5;
        const auto latency = record.receive_time_ns - record.event_time_ns;
        if (latency > maximum_latency_ns_) {
            maximum_latency_ns_ = latency;
        }
        const auto previous = last_prices_.find(record.instrument_id);
        if (previous != last_prices_.end() && previous->second != 0.0) {
            absolute_return_sum_ += std::abs(record.price / previous->second - 1.0);
        }
        last_prices_[record.instrument_id] = record.price;
    }

    py::dict snapshot() const {
        py::dict result;
        result["processed"] = processed_;
        result["sequence_errors"] = sequence_errors_;
        result["instrument_count"] = last_prices_.size();
        result["total_quantity"] = total_quantity_;
        result["total_notional"] = total_notional_;
        result["mean_spread"] = processed_ ? spread_sum_ / processed_ : 0.0;
        result["mean_mid"] = processed_ ? mid_sum_ / processed_ : 0.0;
        result["mean_absolute_return"] = processed_ ? absolute_return_sum_ / processed_ : 0.0;
        result["maximum_latency_ns"] = maximum_latency_ns_;
        return result;
    }

private:
    std::uint64_t processed_{0};
    std::uint64_t sequence_errors_{0};
    std::uint64_t expected_sequence_{0};
    bool has_expected_sequence_{false};
    double total_quantity_{0.0};
    double total_notional_{0.0};
    double spread_sum_{0.0};
    double mid_sum_{0.0};
    double absolute_return_sum_{0.0};
    std::int64_t maximum_latency_ns_{0};
    std::unordered_map<std::uint32_t, double> last_prices_;
};

class TickRing {
public:
    explicit TickRing(std::uint64_t capacity)
        : capacity_(checked_capacity(capacity)), mask_(capacity_ - 1), slots_(capacity_) {}

    std::uint64_t push_batch(py::array_t<TickRecord, py::array::c_style> records) {
        const auto info = records.request();
        const auto count = static_cast<std::uint64_t>(info.size);
        const auto* input = static_cast<const TickRecord*>(info.ptr);
        std::uint64_t pushed = 0;
        {
            py::gil_scoped_release release;
            for (; pushed < count; ++pushed) {
                if (!try_push(input[pushed])) {
                    dropped_.fetch_add(count - pushed, std::memory_order_relaxed);
                    break;
                }
            }
        }
        if (pushed != 0) {
            wakeup_.notify_one();
        }
        return pushed;
    }

    py::array_t<TickRecord> pop_batch(std::uint64_t maximum) {
        const auto available = depth();
        const auto count = available < maximum ? available : maximum;
        if (count > static_cast<std::uint64_t>(std::numeric_limits<py::ssize_t>::max())) {
            throw std::overflow_error("requested tick batch is too large");
        }
        py::array_t<TickRecord> output(static_cast<py::ssize_t>(count));
        auto* destination = static_cast<TickRecord*>(output.request().ptr);
        {
            py::gil_scoped_release release;
            for (std::uint64_t index = 0; index < count; ++index) {
                if (!try_pop(destination[index])) {
                    throw std::runtime_error("tick ring consumer invariant failed");
                }
            }
        }
        return output;
    }

    py::array_t<TickRecord> wait_pop_batch(std::uint64_t maximum, std::uint64_t spin_count, double timeout_seconds) {
        if (!std::isfinite(timeout_seconds) || timeout_seconds < 0) {
            throw std::invalid_argument("timeout_seconds must be finite and non-negative");
        }
        {
            py::gil_scoped_release release;
            for (std::uint64_t attempt = 0; attempt < spin_count; ++attempt) {
                spins_.fetch_add(1, std::memory_order_relaxed);
                if (depth() != 0) {
                    break;
                }
                if ((attempt & 63U) == 63U) {
                    std::this_thread::yield();
                }
            }
            if (depth() == 0 && timeout_seconds > 0) {
                parks_.fetch_add(1, std::memory_order_relaxed);
                std::unique_lock<std::mutex> lock(wait_mutex_);
                wakeup_.wait_for(lock, std::chrono::duration<double>(timeout_seconds), [this] { return depth() != 0; });
            }
        }
        return pop_batch(maximum);
    }

    std::uint64_t process_batch(TickFactorProcessor& processor, std::uint64_t maximum) {
        const auto available = depth();
        const auto count = available < maximum ? available : maximum;
        {
            py::gil_scoped_release release;
            const auto tail = tail_.value.load(std::memory_order_relaxed);
            for (std::uint64_t index = 0; index < count; ++index) {
                processor.process(slots_[(tail + index) & mask_]);
            }
            tail_.value.store(tail + count, std::memory_order_release);
            popped_.fetch_add(count, std::memory_order_relaxed);
        }
        return count;
    }

    std::uint64_t wait_process_batch(
        TickFactorProcessor& processor,
        std::uint64_t maximum,
        std::uint64_t spin_count,
        double timeout_seconds
    ) {
        if (!std::isfinite(timeout_seconds) || timeout_seconds < 0) {
            throw std::invalid_argument("timeout_seconds must be finite and non-negative");
        }
        {
            py::gil_scoped_release release;
            for (std::uint64_t attempt = 0; attempt < spin_count; ++attempt) {
                spins_.fetch_add(1, std::memory_order_relaxed);
                if (depth() != 0) {
                    break;
                }
                if ((attempt & 63U) == 63U) {
                    std::this_thread::yield();
                }
            }
            if (depth() == 0 && timeout_seconds > 0) {
                parks_.fetch_add(1, std::memory_order_relaxed);
                std::unique_lock<std::mutex> lock(wait_mutex_);
                wakeup_.wait_for(lock, std::chrono::duration<double>(timeout_seconds), [this] { return depth() != 0; });
            }
        }
        return process_batch(processor, maximum);
    }

    std::uint64_t capacity() const { return capacity_; }

    std::uint64_t depth() const {
        const auto head = head_.value.load(std::memory_order_acquire);
        const auto tail = tail_.value.load(std::memory_order_acquire);
        return head - tail;
    }

    py::dict metrics() const {
        py::dict result;
        result["capacity"] = capacity_;
        result["depth"] = depth();
        result["pushed"] = pushed_.load(std::memory_order_relaxed);
        result["popped"] = popped_.load(std::memory_order_relaxed);
        result["dropped"] = dropped_.load(std::memory_order_relaxed);
        result["spins"] = spins_.load(std::memory_order_relaxed);
        result["parks"] = parks_.load(std::memory_order_relaxed);
        return result;
    }

private:
    static std::uint64_t checked_capacity(std::uint64_t capacity) {
        if (capacity < 2 || (capacity & (capacity - 1)) != 0 ||
            capacity > std::vector<TickRecord>().max_size()) {
            throw std::invalid_argument("tick ring capacity must be a supported power of two and at least two");
        }
        return capacity;
    }

    struct alignas(64) Cursor {
        std::atomic<std::uint64_t> value{0};
    };

    bool try_push(const TickRecord& record) {
        const auto head = head_.value.load(std::memory_order_relaxed);
        const auto tail = tail_.value.load(std::memory_order_acquire);
        if (head - tail == capacity_) {
            return false;
        }
        slots_[head & mask_] = record;
        head_.value.store(head + 1, std::memory_order_release);
        pushed_.fetch_add(1, std::memory_order_relaxed);
        return true;
    }

    bool try_pop(TickRecord& record) {
        const auto tail = tail_.value.load(std::memory_order_relaxed);
        const auto head = head_.value.load(std::memory_order_acquire);
        if (tail == head) {
            return false;
        }
        record = slots_[tail & mask_];
        tail_.value.store(tail + 1, std::memory_order_release);
        popped_.fetch_add(1, std::memory_order_relaxed);
        return true;
    }

    std::uint64_t capacity_;
    std::uint64_t mask_;
    std::vector<TickRecord> slots_;
    Cursor head_;
    Cursor tail_;
    std::atomic<std::uint64_t> pushed_{0};
    std::atomic<std::uint64_t> popped_{0};
    std::atomic<std::uint64_t> dropped_{0};
    std::atomic<std::uint64_t> spins_{0};
    std::atomic<std::uint64_t> parks_{0};
    std::mutex wait_mutex_;
    std::condition_variable wakeup_;
};

void init_tick_ring(py::module_& module) {
    PYBIND11_NUMPY_DTYPE(
        TickRecord,
        sequence,
        event_time_ns,
        receive_time_ns,
        price,
        quantity,
        bid,
        ask,
        instrument_id,
        flags
    );
    py::class_<TickFactorProcessor>(module, "TickFactorProcessor")
        .def(py::init<>())
        .def_property_readonly("snapshot", &TickFactorProcessor::snapshot);
    py::class_<TickRing>(module, "TickRing")
        .def(py::init<std::uint64_t>(), py::arg("capacity"))
        .def("push_batch", &TickRing::push_batch, py::arg("records"))
        .def("pop_batch", &TickRing::pop_batch, py::arg("maximum"))
        .def(
            "wait_pop_batch",
            &TickRing::wait_pop_batch,
            py::arg("maximum"),
            py::arg("spin_count") = 256,
            py::arg("timeout_seconds") = 0.001
        )
        .def("process_batch", &TickRing::process_batch, py::arg("processor"), py::arg("maximum"))
        .def(
            "wait_process_batch",
            &TickRing::wait_process_batch,
            py::arg("processor"),
            py::arg("maximum"),
            py::arg("spin_count") = 256,
            py::arg("timeout_seconds") = 0.001
        )
        .def_property_readonly("capacity", &TickRing::capacity)
        .def_property_readonly("depth", &TickRing::depth)
        .def_property_readonly("metrics", &TickRing::metrics);
}
