#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <mutex>
#include <stdexcept>
#include <thread>
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

class TickRing {
public:
    explicit TickRing(std::uint64_t capacity)
        : capacity_(capacity), mask_(capacity - 1), slots_(capacity) {
        if (capacity < 2 || (capacity & (capacity - 1)) != 0) {
            throw std::invalid_argument("tick ring capacity must be a power of two and at least two");
        }
    }

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
        if (timeout_seconds < 0) {
            throw std::invalid_argument("timeout_seconds must be non-negative");
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
        .def_property_readonly("capacity", &TickRing::capacity)
        .def_property_readonly("depth", &TickRing::depth)
        .def_property_readonly("metrics", &TickRing::metrics);
}
