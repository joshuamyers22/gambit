// Experimental, long-only alternating-target strategy and top-of-book replay.
// All monetary values use price ticks * quantity lots; no floating point math.
#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <limits>
#include <mutex>
#include <stdexcept>
#include <vector>

namespace py = pybind11;

namespace {
struct BookEvent {
    std::uint64_t sequence;
    std::int64_t event_time_ns, receive_time_ns;
    std::int64_t bid, ask, bid_size, ask_size;
    std::uint32_t instrument_id, flags;
};
static_assert(sizeof(BookEvent) == 64, "book event layout changed");

// Status: 0 open, 1 filled, 2 cancelled/replaced, 3 risk rejected.
struct ReplayOrder {
    std::uint64_t id, sequence;
    std::int64_t timestamp_ns, quantity, remaining;
    std::uint32_t instrument_id, status;
};
struct ReplayFill {
    std::uint64_t order_id, sequence;
    std::int64_t timestamp_ns, quantity, price, fee;
    std::uint32_t instrument_id, reserved;
};
struct InstrumentState {
    std::int64_t position{0}, bid{0};
    std::uint64_t countdown{0}, pending{0};
    bool target_long{false};
};

std::int64_t checked_add(std::int64_t a, std::int64_t b) {
    if (a < 0 || b < 0 || a > std::numeric_limits<std::int64_t>::max() - b) {
        throw std::overflow_error("backtest monetary addition overflow");
    }
    return a + b;
}
std::int64_t checked_product(std::int64_t a, std::int64_t b) {
    if (a < 0 || b < 0 || (b && a > std::numeric_limits<std::int64_t>::max() / b)) {
        throw std::overflow_error("backtest monetary multiplication overflow");
    }
    return a * b;
}

class TopOfBookBacktester {
public:
    TopOfBookBacktester(std::uint32_t instruments, std::int64_t cash,
                       std::int64_t target_lots, std::uint64_t rebalance_events,
                       std::uint32_t fee_ppm, std::int64_t latency_ns,
                       std::uint64_t audit_capacity, std::int64_t maximum_feed_age_ns)
        : initial_cash_(cash), cash_(cash), target_lots_(target_lots),
          interval_(rebalance_events), fee_ppm_(fee_ppm), latency_ns_(latency_ns),
          audit_capacity_(audit_capacity), maximum_feed_age_ns_(maximum_feed_age_ns) {
        if (!instruments || instruments > 4096 || cash < 0 || target_lots <= 0 ||
            !rebalance_events || fee_ppm > 1000000 || latency_ns < 0 ||
            !audit_capacity || audit_capacity > 10000000 || maximum_feed_age_ns < 0) {
            throw std::invalid_argument("invalid top-of-book backtest configuration");
        }
        instruments_.resize(instruments);
        for (auto& state : instruments_) state.countdown = interval_;
        orders_.reserve(static_cast<std::size_t>(audit_capacity));
        fills_.reserve(static_cast<std::size_t>(audit_capacity));
    }

    std::uint64_t process_batch(py::array_t<BookEvent, py::array::c_style> events) {
        const auto info = events.request();
        if (info.ndim != 1 || reinterpret_cast<std::uintptr_t>(info.ptr) % alignof(BookEvent)) {
            throw std::invalid_argument("book events must be one-dimensional and aligned");
        }
        auto access = acquire();
        if (failed_) throw std::runtime_error("backtest is failed; create a new instance");
        const auto count = static_cast<std::uint64_t>(info.size);
        const auto* input = static_cast<const BookEvent*>(info.ptr);
        py::gil_scoped_release release;
        try {
            for (std::uint64_t index = 0; index < count; ++index) process(input[index]);
        } catch (...) {
            // Earlier events may have been applied. Never publish them as a valid run.
            failed_ = true;
            throw;
        }
        return count;
    }

    py::dict result() const {
        auto access = acquire();
        if (failed_) throw std::runtime_error("cannot publish a failed backtest");
        std::int64_t equity = cash_;
        py::array_t<std::int64_t> positions(instruments_.size());
        auto* position_data = positions.mutable_data();
        try {
            for (std::size_t i = 0; i < instruments_.size(); ++i) {
                const auto& state = instruments_[i];
                equity = checked_add(equity, checked_product(state.position, state.bid));
                position_data[i] = state.position;
            }
        } catch (...) {
            failed_ = true;
            throw;
        }
        py::array_t<ReplayOrder> orders(orders_.size());
        py::array_t<ReplayFill> fills(fills_.size());
        if (!orders_.empty()) std::memcpy(orders.mutable_data(), orders_.data(), orders_.size() * sizeof(ReplayOrder));
        if (!fills_.empty()) std::memcpy(fills.mutable_data(), fills_.data(), fills_.size() * sizeof(ReplayFill));
        positions.attr("setflags")(false);
        orders.attr("setflags")(false);
        fills.attr("setflags")(false);
        py::dict out;
        out["processed"] = processed_;
        out["cash"] = cash_;
        out["equity"] = equity;
        out["net_pnl"] = equity - initial_cash_;
        out["total_fees"] = total_fees_;
        out["positions"] = positions;
        out["orders"] = orders;
        out["fills"] = fills;
        return out;
    }

private:
    std::unique_lock<std::mutex> acquire() const {
        std::unique_lock<std::mutex> access(mutex_, std::try_to_lock);
        if (!access.owns_lock()) throw std::runtime_error("backtest is already in use");
        return access;
    }

    void process(const BookEvent& event) {
        if (event.sequence != processed_ || processed_ == std::numeric_limits<std::uint64_t>::max() ||
            event.instrument_id >= instruments_.size() || event.flags ||
            event.event_time_ns < 0 || event.receive_time_ns < event.event_time_ns ||
            event.receive_time_ns - event.event_time_ns > maximum_feed_age_ns_ ||
            (processed_ && event.receive_time_ns < last_receive_time_) ||
            event.bid <= 0 || event.ask < event.bid || event.bid_size < 0 || event.ask_size < 0) {
            throw std::invalid_argument("invalid, gapped, reordered, or unsupported book event");
        }
        auto& state = instruments_[event.instrument_id];
        state.bid = event.bid;
        if (state.pending) {
            auto& order = orders_[static_cast<std::size_t>(state.pending - 1)];
            // Difference cannot overflow: both receive times are nonnegative and ordered.
            if (event.receive_time_ns - order.timestamp_ns >= latency_ns_) {
                fill(event, state, order);
            }
        }
        if (--state.countdown == 0) {
            state.countdown = interval_;
            state.target_long = !state.target_long;
            if (state.pending) {
                orders_[static_cast<std::size_t>(state.pending - 1)].status = 2;
                state.pending = 0;
            }
            const auto quantity = (state.target_long ? target_lots_ : 0) - state.position;
            if (quantity) {
                if (orders_.size() == audit_capacity_) throw std::runtime_error("order audit capacity exhausted");
                const auto id = static_cast<std::uint64_t>(orders_.size()) + 1;
                orders_.push_back({id, event.sequence, event.receive_time_ns, quantity, quantity, event.instrument_id, 0});
                state.pending = id;
            }
        }
        last_receive_time_ = event.receive_time_ns;
        ++processed_;
    }

    void fill(const BookEvent& event, InstrumentState& state, ReplayOrder& order) {
        const bool buy = order.remaining > 0;
        const auto available = buy ? event.ask_size : event.bid_size;
        const auto quantity = std::min(buy ? order.remaining : -order.remaining, available);
        if (!quantity) return;
        const auto price = buy ? event.ask : event.bid;
        const auto notional = checked_product(quantity, price);
        // Nonnegative fees, rounded up to the smallest configured monetary unit.
        const auto fee = (notional / 1000000) * fee_ppm_ +
                         ((notional % 1000000) * fee_ppm_ + 999999) / 1000000;
        if (buy && (notional > cash_ || fee > cash_ - notional)) {
            order.status = 3;
            state.pending = 0;
            return;
        }
        if (fills_.size() == audit_capacity_) throw std::runtime_error("fill audit capacity exhausted");
        const auto next_cash = buy ? cash_ - notional - fee : checked_add(cash_, notional - fee);
        const auto next_fees = checked_add(total_fees_, fee);
        const auto signed_quantity = buy ? quantity : -quantity;
        state.position += signed_quantity;  // bounded by the long-only target
        order.remaining -= signed_quantity;
        cash_ = next_cash;
        total_fees_ = next_fees;
        fills_.push_back({order.id, event.sequence, event.receive_time_ns, signed_quantity, price, fee, event.instrument_id, 0});
        if (!order.remaining) {
            order.status = 1;
            state.pending = 0;
        }
    }

    mutable std::mutex mutex_;
    std::vector<InstrumentState> instruments_;
    std::vector<ReplayOrder> orders_;
    std::vector<ReplayFill> fills_;
    std::int64_t initial_cash_, cash_, target_lots_;
    std::uint64_t interval_;
    std::uint32_t fee_ppm_;
    std::int64_t latency_ns_;
    std::uint64_t audit_capacity_, processed_{0};
    std::int64_t maximum_feed_age_ns_;
    std::int64_t last_receive_time_{0}, total_fees_{0};
    mutable bool failed_{false};
};
}  // namespace

void init_top_of_book_backtest(py::module_& module) {
    PYBIND11_NUMPY_DTYPE(BookEvent, sequence, event_time_ns, receive_time_ns, bid, ask, bid_size, ask_size, instrument_id, flags);
    PYBIND11_NUMPY_DTYPE(ReplayOrder, id, sequence, timestamp_ns, quantity, remaining, instrument_id, status);
    PYBIND11_NUMPY_DTYPE(ReplayFill, order_id, sequence, timestamp_ns, quantity, price, fee, instrument_id, reserved);
    py::class_<TopOfBookBacktester>(module, "TopOfBookBacktester")
        .def(py::init<std::uint32_t, std::int64_t, std::int64_t, std::uint64_t, std::uint32_t, std::int64_t, std::uint64_t, std::int64_t>(),
             py::arg("instruments"), py::arg("cash"), py::arg("target_lots"), py::arg("rebalance_events"),
             py::arg("fee_ppm") = 0, py::arg("latency_ns") = 0, py::arg("audit_capacity") = 100000,
             py::arg("maximum_feed_age_ns") = 1000000000)
        .def("process_batch", &TopOfBookBacktester::process_batch, py::arg("events").noconvert())
        .def("result", &TopOfBookBacktester::result);
}
