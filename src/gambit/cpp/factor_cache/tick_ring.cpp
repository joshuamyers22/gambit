#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>

#include "spsc_ring.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cmath>
#include <limits>
#include <mutex>
#include <memory>
#include <stdexcept>
#include <thread>
#include <unordered_map>

namespace py = pybind11;

class TickRing;
#if defined(__GNUC__)
#define GAMBIT_HIDDEN __attribute__((visibility("hidden")))
#else
#define GAMBIT_HIDDEN
#endif
struct GAMBIT_HIDDEN TickBatchLeaseState;

class GAMBIT_HIDDEN TickBatchLease {
public:
    explicit TickBatchLease(std::shared_ptr<TickBatchLeaseState> state)
        : state_(std::move(state)) {}
    TickBatchLease(const TickBatchLease&) = delete;
    TickBatchLease& operator=(const TickBatchLease&) = delete;
    TickBatchLease(TickBatchLease&&) noexcept = default;
    TickBatchLease& operator=(TickBatchLease&&) = delete;
    ~TickBatchLease();
    py::array values();
    void close();
    bool closed() const;
    std::uint64_t size() const;

private:
    std::shared_ptr<TickBatchLeaseState> state_;
};

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
    std::unique_lock<std::mutex> acquire_access() const {
        std::unique_lock<std::mutex> access(access_mutex_, std::try_to_lock);
        if (!access.owns_lock()) {
            throw std::runtime_error("tick processor is already in use by another operation");
        }
        return access;
    }

    std::uint64_t process_batch(py::array_t<TickRecord, py::array::c_style> records) {
        const auto info = records.request();
        if (info.ndim != 1) {
            throw std::invalid_argument("tick records must be a one-dimensional array");
        }
        if (reinterpret_cast<std::uintptr_t>(info.ptr) % alignof(TickRecord) != 0) {
            throw std::invalid_argument("tick records must be aligned for native access");
        }
        const auto count = static_cast<std::uint64_t>(info.size);
        const auto* input = static_cast<const TickRecord*>(info.ptr);
        auto access = acquire_access();
        {
            py::gil_scoped_release release;
            for (std::uint64_t index = 0; index < count; ++index) {
                process(input[index]);
            }
        }
        return count;
    }

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
        auto access = acquire_access();
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
    mutable std::mutex access_mutex_;
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
        : ring_(capacity) {}

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
            {
                // Serialize with the consumer's predicate check before notifying.
                std::lock_guard<std::mutex> lock(wait_mutex_);
            }
            wakeup_.notify_one();
        }
        return pushed;
    }

    py::array_t<TickRecord> pop_batch(std::uint64_t maximum) {
        reject_active_lease();
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

    py::array_t<TickRecord> wait_pop_batch(
        std::uint64_t maximum,
        std::uint64_t spin_count,
        double timeout_seconds,
        std::uint64_t backoff_count,
        double maximum_backoff_seconds
    ) {
        validate_wait_arguments(timeout_seconds, maximum_backoff_seconds);
        {
            py::gil_scoped_release release;
            wait_for_data(spin_count, backoff_count, maximum_backoff_seconds, timeout_seconds);
        }
        return pop_batch(maximum);
    }

    std::uint64_t process_batch(TickFactorProcessor& processor, std::uint64_t maximum) {
        reject_active_lease();
        auto access = processor.acquire_access();
        const auto available = depth();
        const auto count = available < maximum ? available : maximum;
        {
            py::gil_scoped_release release;
            const auto consumed = ring_.consume(count, [&processor](const TickRecord& record) {
                processor.process(record);
            });
            if (consumed != count) {
                throw std::runtime_error("tick ring consumer invariant failed");
            }
            popped_.fetch_add(consumed, std::memory_order_relaxed);
        }
        return count;
    }

    std::uint64_t wait_process_batch(
        TickFactorProcessor& processor,
        std::uint64_t maximum,
        std::uint64_t spin_count,
        double timeout_seconds,
        std::uint64_t backoff_count,
        double maximum_backoff_seconds
    ) {
        validate_wait_arguments(timeout_seconds, maximum_backoff_seconds);
        {
            py::gil_scoped_release release;
            wait_for_data(spin_count, backoff_count, maximum_backoff_seconds, timeout_seconds);
        }
        return process_batch(processor, maximum);
    }

    void close() {
        {
            std::lock_guard<std::mutex> lock(wait_mutex_);
            closed_.store(true, std::memory_order_release);
        }
        wakeup_.notify_all();
    }

    TickBatchLease lease_batch(std::uint64_t maximum);

    TickBatchLease wait_lease_batch(
        std::uint64_t maximum,
        std::uint64_t spin_count,
        double timeout_seconds,
        std::uint64_t backoff_count,
        double maximum_backoff_seconds
    ) {
        validate_wait_arguments(timeout_seconds, maximum_backoff_seconds);
        {
            py::gil_scoped_release release;
            wait_for_data(spin_count, backoff_count, maximum_backoff_seconds, timeout_seconds);
        }
        return lease_batch(maximum);
    }

    void release_lease(std::uint64_t count) {
        ring_.release(count);
        popped_.fetch_add(count, std::memory_order_relaxed);
        lease_active_.store(false, std::memory_order_release);
    }

    std::uint64_t capacity() const { return ring_.capacity(); }

    std::uint64_t depth() const {
        return ring_.depth();
    }

    py::dict metrics() const {
        py::dict result;
        result["capacity"] = capacity();
        result["depth"] = depth();
        result["pushed"] = pushed_.load(std::memory_order_relaxed);
        result["popped"] = popped_.load(std::memory_order_relaxed);
        result["dropped"] = dropped_.load(std::memory_order_relaxed);
        result["spins"] = spins_.load(std::memory_order_relaxed);
        result["yields"] = yields_.load(std::memory_order_relaxed);
        result["backoffs"] = backoffs_.load(std::memory_order_relaxed);
        result["parks"] = parks_.load(std::memory_order_relaxed);
        result["park_timeouts"] = park_timeouts_.load(std::memory_order_relaxed);
        result["wakeups"] = wakeups_.load(std::memory_order_relaxed);
        result["closed"] = closed_.load(std::memory_order_acquire);
        result["active_lease"] = lease_active_.load(std::memory_order_acquire);
        return result;
    }

private:
    void reject_active_lease() const {
        if (lease_active_.load(std::memory_order_acquire)) {
            throw std::runtime_error("a tick batch lease is already active");
        }
    }
    static void validate_wait_arguments(double timeout_seconds, double maximum_backoff_seconds) {
        if (!std::isfinite(timeout_seconds) || timeout_seconds < 0) {
            throw std::invalid_argument("timeout_seconds must be finite and non-negative");
        }
        if (!std::isfinite(maximum_backoff_seconds) || maximum_backoff_seconds < 0) {
            throw std::invalid_argument("maximum_backoff_seconds must be finite and non-negative");
        }
    }

    bool data_or_closed() const {
        return depth() != 0 || closed_.load(std::memory_order_acquire);
    }

    void wait_for_data(
        std::uint64_t spin_count,
        std::uint64_t backoff_count,
        double maximum_backoff_seconds,
        double timeout_seconds
    ) {
        for (std::uint64_t attempt = 0; attempt < spin_count && !data_or_closed(); ++attempt) {
            spins_.fetch_add(1, std::memory_order_relaxed);
            if ((attempt & 63U) == 63U) {
                yields_.fetch_add(1, std::memory_order_relaxed);
                std::this_thread::yield();
            }
        }
        double delay_seconds = std::min(0.000001, maximum_backoff_seconds);
        for (
            std::uint64_t attempt = 0;
            attempt < backoff_count && maximum_backoff_seconds > 0 && !data_or_closed();
            ++attempt
        ) {
            backoffs_.fetch_add(1, std::memory_order_relaxed);
            std::this_thread::sleep_for(std::chrono::duration<double>(delay_seconds));
            delay_seconds = std::min(maximum_backoff_seconds, delay_seconds * 2.0);
        }
        if (!data_or_closed() && timeout_seconds > 0) {
            parks_.fetch_add(1, std::memory_order_relaxed);
            std::unique_lock<std::mutex> lock(wait_mutex_);
            const bool awakened = wakeup_.wait_for(
                lock,
                std::chrono::duration<double>(timeout_seconds),
                [this] { return data_or_closed(); }
            );
            if (awakened) {
                wakeups_.fetch_add(1, std::memory_order_relaxed);
            } else {
                park_timeouts_.fetch_add(1, std::memory_order_relaxed);
            }
        }
    }

    bool try_push(const TickRecord& record) {
        if (closed_.load(std::memory_order_acquire)) {
            return false;
        }
        if (!ring_.try_push(record)) {
            return false;
        }
        pushed_.fetch_add(1, std::memory_order_relaxed);
        return true;
    }

    bool try_pop(TickRecord& record) {
        if (!ring_.try_pop(record)) {
            return false;
        }
        popped_.fetch_add(1, std::memory_order_relaxed);
        return true;
    }

    gambit::SpscRing<TickRecord> ring_;
    std::atomic<std::uint64_t> pushed_{0};
    std::atomic<std::uint64_t> popped_{0};
    std::atomic<std::uint64_t> dropped_{0};
    std::atomic<std::uint64_t> spins_{0};
    std::atomic<std::uint64_t> yields_{0};
    std::atomic<std::uint64_t> backoffs_{0};
    std::atomic<std::uint64_t> parks_{0};
    std::atomic<std::uint64_t> park_timeouts_{0};
    std::atomic<std::uint64_t> wakeups_{0};
    std::atomic<bool> closed_{false};
    std::atomic<bool> lease_active_{false};
    std::mutex wait_mutex_;
    std::condition_variable wakeup_;
};

struct GAMBIT_HIDDEN TickBatchLeaseState {
    TickBatchLeaseState(
        TickRing* ring_value,
        py::object owner_value,
        const TickRecord* data_value,
        std::uint64_t count_value
    )
        : ring(ring_value), owner(std::move(owner_value)), data(data_value), count(count_value) {}

    TickRing* ring;
    py::object owner;
    const TickRecord* data;
    std::uint64_t count;
    std::uint64_t views{0};
    bool close_requested{false};
    bool released{false};

    void release_if_ready() {
        if (close_requested && views == 0 && !released) {
            ring->release_lease(count);
            released = true;
        }
    }

    void close() {
        close_requested = true;
        release_if_ready();
    }

    void view_closed() {
        if (views == 0) {
            return;
        }
        --views;
        release_if_ready();
    }
};

TickBatchLease::~TickBatchLease() {
    if (state_) {
        state_->close();
    }
}

void TickBatchLease::close() {
    state_->close();
}

bool TickBatchLease::closed() const {
    return state_->close_requested;
}

std::uint64_t TickBatchLease::size() const {
    return state_->count;
}

py::array TickBatchLease::values() {
    if (state_->close_requested) {
        throw std::runtime_error("tick batch lease is closed");
    }
    ++state_->views;
    auto* retained = new std::shared_ptr<TickBatchLeaseState>(state_);
    py::capsule base(retained, [](void* pointer) {
        auto* state = static_cast<std::shared_ptr<TickBatchLeaseState>*>(pointer);
        (*state)->view_closed();
        delete state;
    });
    py::array result(
        py::dtype::of<TickRecord>(),
        {static_cast<py::ssize_t>(state_->count)},
        {static_cast<py::ssize_t>(sizeof(TickRecord))},
        state_->data,
        base
    );
    result.attr("setflags")(false);
    return result;
}

TickBatchLease TickRing::lease_batch(std::uint64_t maximum) {
    bool expected = false;
    if (!lease_active_.compare_exchange_strong(
            expected, true, std::memory_order_acq_rel, std::memory_order_acquire)) {
        throw std::runtime_error("a tick batch lease is already active");
    }
    try {
        const auto span = ring_.read_span(maximum);
        auto owner = py::cast(this, py::return_value_policy::reference);
        auto state = std::make_shared<TickBatchLeaseState>(
            this, std::move(owner), span.data, span.count
        );
        return TickBatchLease(std::move(state));
    } catch (...) {
        lease_active_.store(false, std::memory_order_release);
        throw;
    }
}

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
        .def("process_batch", &TickFactorProcessor::process_batch, py::arg("records").noconvert(),
             "Process a contiguous, aligned, one-dimensional tick array without ring transport. "
             "The caller must not modify the array during processing. Concurrent access to "
             "the same processor is rejected; the GIL is released during calculation.")
        .def_property_readonly("snapshot", &TickFactorProcessor::snapshot);
    py::class_<TickBatchLease>(module, "TickBatchLease")
        .def_property_readonly("values", &TickBatchLease::values)
        .def("close", &TickBatchLease::close)
        .def(
            "__enter__",
            [](TickBatchLease& self) -> TickBatchLease& { return self; },
            py::return_value_policy::reference_internal
        )
        .def("__exit__", [](TickBatchLease& self, py::object, py::object, py::object) { self.close(); })
        .def_property_readonly("closed", &TickBatchLease::closed)
        .def_property_readonly("size", &TickBatchLease::size);
    py::class_<TickRing>(module, "TickRing")
        .def(py::init<std::uint64_t>(), py::arg("capacity"))
        .def("push_batch", &TickRing::push_batch, py::arg("records"))
        .def("pop_batch", &TickRing::pop_batch, py::arg("maximum"))
        .def(
            "wait_pop_batch",
            &TickRing::wait_pop_batch,
            py::arg("maximum"),
            py::arg("spin_count") = 256,
            py::arg("timeout_seconds") = 0.001,
            py::arg("backoff_count") = 0,
            py::arg("maximum_backoff_seconds") = 0.0
        )
        .def("process_batch", &TickRing::process_batch, py::arg("processor"), py::arg("maximum"))
        .def(
            "wait_process_batch",
            &TickRing::wait_process_batch,
            py::arg("processor"),
            py::arg("maximum"),
            py::arg("spin_count") = 256,
            py::arg("timeout_seconds") = 0.001,
            py::arg("backoff_count") = 0,
            py::arg("maximum_backoff_seconds") = 0.0
        )
        .def("close", &TickRing::close)
        .def("lease_batch", &TickRing::lease_batch, py::arg("maximum"))
        .def(
            "wait_lease_batch",
            &TickRing::wait_lease_batch,
            py::arg("maximum"),
            py::arg("spin_count") = 256,
            py::arg("timeout_seconds") = 0.001,
            py::arg("backoff_count") = 0,
            py::arg("maximum_backoff_seconds") = 0.0
        )
        .def_property_readonly("capacity", &TickRing::capacity)
        .def_property_readonly("depth", &TickRing::depth)
        .def_property_readonly("metrics", &TickRing::metrics);
}
