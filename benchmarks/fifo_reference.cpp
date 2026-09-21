// Standalone LAT-04 policy oracle. No Gambit headers, native calls, or shared
// execution helpers. Input/output are explicitly encoded little-endian words.
// Event counts/modulo and a map of active orders follow the Python specification;
// monetary expressions use 128-bit intermediates, not the native overflow helpers.
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <limits>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

using U = std::uint64_t;
using I = std::int64_t;
using Wide = __int128_t;
const U MAX_WORD = std::numeric_limits<U>::max();
const I MAX_MONEY = std::numeric_limits<I>::max();

U word(const unsigned char* p, unsigned bytes = 8) {
    U value = 0;
    for (unsigned n = 0; n < bytes; ++n) value |= U(p[n]) << (8 * n);
    return value;
}
I signed_word(const unsigned char* p) {
    const U bits = word(p);
    I value;
    std::memcpy(&value, &bits, sizeof(value));
    return value;
}
void read_exact(void* target, std::size_t bytes) {
    if (std::fread(target, 1, bytes, stdin) != bytes) throw std::runtime_error("truncated input protocol");
}
I money(Wide value) {
    if (value < 0 || value > MAX_MONEY) throw std::overflow_error("monetary range");
    return static_cast<I>(value);
}
U argument(const char* text) {
    const std::string value(text);
    if (value.empty() || value.find_first_not_of("0123456789") != std::string::npos)
        throw std::invalid_argument("unsigned decimal configuration required");
    return std::stoull(value);
}
struct Order {
    U number, sequence;
    I time, quantity, remaining;
    U instrument, status;
};
struct Queue {
    I limit, ahead, initial;
    U sequence;
    I time;
};
struct Fill {
    U order, sequence;
    I time, quantity, price, fee;
    U instrument;
};

class Reference {
    U instrument_count, interval, fee_rate, capacity;
    I initial_cash, cash, target, latency, maximum_age, fees = 0, last_time = 0;
    U processed = 0;
    std::vector<I> positions, marks;
    std::vector<U> observations;
    std::map<U, std::size_t> active;
    std::vector<Order> orders;
    std::vector<Queue> queues;
    std::vector<Fill> fills;

public:
    explicit Reference(char** argv) {
        instrument_count = argument(argv[1]);
        initial_cash = cash = money(argument(argv[2]));
        target = money(argument(argv[3]));
        interval = argument(argv[4]);
        fee_rate = argument(argv[5]);
        latency = money(argument(argv[6]));
        capacity = argument(argv[7]);
        maximum_age = money(argument(argv[8]));
        if (!instrument_count || instrument_count > 4096 || !target || !interval ||
            fee_rate > 1000000 || !capacity || capacity > 1000000)
            throw std::invalid_argument("configuration outside bounded FIFO reference");
        positions.resize(instrument_count);
        marks.resize(instrument_count);
        observations.resize(instrument_count);
    }

    void event(const unsigned char* p) {
        const U sequence = word(p), instrument = word(p + 56, 4);
        const I source_time = signed_word(p + 8), now = signed_word(p + 16);
        const I bid = signed_word(p + 24), ask = signed_word(p + 32);
        const I bid_size = signed_word(p + 40), ask_size = signed_word(p + 48);
        const I trade_price = signed_word(p + 64), trade_size = signed_word(p + 72);
        const U side_bits = word(p + 80, 4);
        const int side = side_bits == 0xffffffffULL ? -1 : (side_bits == 1 ? 1 : 0);
        if (word(p + 84, 4) || trade_size < 0 ||
            (trade_size == 0 && (side_bits || trade_price)) ||
            (trade_size > 0 && (trade_price <= 0 || side == 0)))
            throw std::invalid_argument("invalid trade");
        if (sequence != processed || processed == MAX_WORD || instrument >= instrument_count ||
            word(p + 60, 4) || source_time < 0 || now < source_time ||
            now - source_time > maximum_age || (processed && now < last_time) ||
            bid <= 0 || ask < bid || bid_size < 0 || ask_size < 0)
            throw std::invalid_argument("invalid book/sequence/time");
        marks[instrument] = bid;
        const auto found = active.find(instrument);
        if (found != active.end()) {
            const std::size_t index = found->second;
            Order& order = orders[index];
            Queue& queue = queues[index];
            const bool buy = order.remaining > 0;
            if (queue.time == -1) {
                // Wide sum implements the eligibility predicate independently.
                if (Wide(now) >= Wide(order.time) + latency) {
                    if (queue.limit != (buy ? bid : ask) || (buy ? queue.limit >= ask : queue.limit <= bid)) {
                        order.status = 4;
                        active.erase(instrument);
                    } else {
                        queue.ahead = queue.initial = buy ? bid_size : ask_size;
                        queue.sequence = sequence;
                        queue.time = now;
                    }
                }
            } else if (trade_price == queue.limit && side == (buy ? -1 : 1)) {
                const I executable = std::max<I>(0, trade_size - queue.ahead);
                queue.ahead = std::max<I>(0, queue.ahead - trade_size);
                const I amount = std::min<I>(buy ? order.remaining : -order.remaining, executable);
                if (amount) {
                    const I notional = money(Wide(amount) * queue.limit);
                    const I fee = money((Wide(notional) * fee_rate + 999999) / 1000000);
                    if (buy && Wide(notional) + fee > cash) {
                        order.status = 3;
                        active.erase(instrument);
                    } else {
                        if (fills.size() == capacity) throw std::runtime_error("fill capacity");
                        const I quantity = buy ? amount : -amount;
                        cash = money(Wide(cash) - Wide(quantity) * queue.limit - fee);
                        fees = money(Wide(fees) + fee);
                        positions[instrument] += quantity;
                        order.remaining -= quantity;
                        fills.push_back({order.number, sequence, now, quantity, queue.limit, fee, instrument});
                        if (!order.remaining) {
                            order.status = 1;
                            active.erase(instrument);
                        }
                    }
                }
            }
        }
        ++observations[instrument];
        if (observations[instrument] % interval == 0) {
            const auto previous = active.find(instrument);
            if (previous != active.end()) {
                orders[previous->second].status = 2;
                active.erase(previous);
            }
            const I desired = (observations[instrument] / interval) % 2 ? target : 0;
            const I quantity = desired - positions[instrument];
            if (quantity) {
                if (orders.size() == capacity) throw std::runtime_error("order capacity");
                active[instrument] = orders.size();
                orders.push_back({orders.size() + 1, sequence, now, quantity, quantity, instrument, 0});
                queues.push_back({quantity > 0 ? bid : ask, 0, 0, MAX_WORD, -1});
            }
        }
        ++processed;
        last_time = now;
    }

    void snapshot() const {
        I equity = cash;
        for (U n = 0; n < instrument_count; ++n)
            equity = money(Wide(equity) + money(Wide(positions[n]) * marks[n]));
        std::vector<unsigned char> bytes{'G', 'F', 'I', 'F', 'O', '0', '0', '1'};
        bytes.reserve(80 + positions.size() * 8 + orders.size() * 56 + fills.size() * 64 + queues.size() * 40);
        const auto append = [&bytes](U value) {
            for (unsigned n = 0; n < 8; ++n) bytes.push_back(static_cast<unsigned char>(value >> (8 * n)));
        };
        for (U value : {processed, U(cash), U(equity), U(equity - initial_cash), U(fees),
                        instrument_count, U(orders.size()), U(fills.size()), U(queues.size())}) append(value);
        for (I value : positions) append(U(value));
        for (const auto& order : orders)
            for (U value : {order.number, order.sequence, U(order.time), U(order.quantity), U(order.remaining),
                            order.instrument, order.status}) append(value);
        for (const auto& fill : fills)
            for (U value : {fill.order, fill.sequence, U(fill.time), U(fill.quantity), U(fill.price), U(fill.fee),
                            fill.instrument, U(0)}) append(value);
        for (const auto& queue : queues)
            for (U value : {U(queue.limit), U(queue.ahead), U(queue.initial), queue.sequence, U(queue.time)}) append(value);
        if (std::fwrite(bytes.data(), 1, bytes.size(), stdout) != bytes.size() || std::fflush(stdout))
            throw std::runtime_error("snapshot write failed");
    }
};

int main(int argc, char** argv) {
    try {
        if (argc != 9) throw std::invalid_argument("eight configuration arguments required");
        Reference reference(argv);
        std::vector<unsigned char> input;
        for (;;) {
            unsigned char header[8];
            read_exact(header, sizeof(header));
            const U count = word(header);
            if (count == MAX_WORD) return 0;
            if (!count) { reference.snapshot(); continue; }
            if (count > 1048576) throw std::invalid_argument("input chunk exceeds bound");
            input.resize(static_cast<std::size_t>(count) * 88);
            read_exact(input.data(), input.size());
            for (U n = 0; n < count; ++n) reference.event(input.data() + n * 88);
        }
    } catch (const std::exception& error) {
        std::fprintf(stderr, "reference failed: %s\n", error.what());
        return 2;
    }
}
