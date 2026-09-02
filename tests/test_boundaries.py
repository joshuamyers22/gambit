from types import SimpleNamespace

import numpy as np
import pytest

from gambit.account import Account
from gambit.boundaries import BacktestCallbackError
from gambit.callback_contracts import validate_market_trades, validate_rule_orders
from gambit.pq_types import Contract, ContractGroup, MarketOrder, OrderStatus, Trade
from gambit.strategy import Strategy
from gambit.strategy_components import SimpleMarketSimulator
from gambit.strategy_inputs import PriceFuncArrayDict, PriceFuncDict, VectorIndicator, VectorSignal


def _price(_contract, _timestamps, _index, _context):
    return 100.0


@pytest.mark.parametrize(
    ("timestamps", "error", "message"),
    [
        (np.array([], dtype="datetime64[ns]"), ValueError, "cannot be empty"),
        (np.array(["NaT"], dtype="datetime64[ns]"), ValueError, "cannot contain NaT"),
        (
            np.array(["2026-01-01", "2026-01-01"], dtype="datetime64[D]"),
            ValueError,
            "strictly increasing",
        ),
        (np.array([1, 2]), TypeError, "datetime64 dtype"),
        (np.array([["2026-01-01"]], dtype="datetime64[D]"), ValueError, "one-dimensional"),
    ],
)
def test_strategy_rejects_invalid_timestamp_grids(timestamps, error, message) -> None:
    group = ContractGroup.get("timestamp-boundary")

    with pytest.raises(error, match=message):
        Strategy(timestamps, [group], _price)


@pytest.mark.parametrize(
    ("timestamps", "error", "message"),
    [
        (np.array([], dtype="datetime64[ns]"), ValueError, "account timestamps cannot be empty"),
        (
            np.array(["2026-01-01", "2026-01-01"], dtype="datetime64[D]"),
            ValueError,
            "account timestamps must be strictly increasing",
        ),
        (np.array([1, 2]), TypeError, "account timestamps must have a datetime64 dtype"),
    ],
)
def test_account_rejects_invalid_timestamp_grids(timestamps, error, message) -> None:
    group = ContractGroup.get("account-timestamp-boundary")

    with pytest.raises(error, match=message):
        Account([group], timestamps, _price, SimpleNamespace())


@pytest.mark.parametrize(
    ("contract_groups", "error", "message"),
    [
        ([], ValueError, "requires at least one contract group"),
        ([object()], TypeError, "only ContractGroup objects"),
        ("not-a-sequence-of-groups", TypeError, "must be a sequence"),
    ],
)
def test_account_rejects_invalid_contract_groups(contract_groups, error, message) -> None:
    timestamps = np.array(["2026-01-01"], dtype="datetime64[D]")

    with pytest.raises(error, match=message):
        Account(contract_groups, timestamps, _price, SimpleNamespace())


def test_account_rejects_duplicate_contract_group_names() -> None:
    timestamps = np.array(["2026-01-01"], dtype="datetime64[D]")
    first = ContractGroup("duplicate-account-group")
    second = ContractGroup("duplicate-account-group")

    with pytest.raises(ValueError, match="contract group names must be unique"):
        Account([first, second], timestamps, _price, SimpleNamespace())


def test_strategy_owns_validated_contract_group_snapshot() -> None:
    timestamp = np.datetime64("2026-01-01")
    configured_group = ContractGroup.get("strategy-configured-group")
    caller_groups = [configured_group]

    strategy = Strategy(np.array([timestamp]), caller_groups, _price)
    caller_groups.append(ContractGroup.get("late-caller-group"))

    assert strategy.contract_groups == (configured_group,)
    assert strategy.contract_groups is strategy.account.contract_groups


def test_strategy_owns_read_only_timestamp_snapshot() -> None:
    caller_timestamps = np.array(["2026-01-01", "2026-01-02"], dtype="datetime64[D]")
    original_timestamps = caller_timestamps.copy()
    strategy = Strategy(
        caller_timestamps,
        [ContractGroup.get("strategy-timestamp-snapshot")],
        _price,
    )

    caller_timestamps[0] = np.datetime64("2030-01-01")

    assert np.array_equal(strategy.timestamps, original_timestamps)
    assert strategy.timestamps is strategy.account.timestamps
    with pytest.raises(ValueError, match="read-only"):
        strategy.timestamps[0] = np.datetime64("2030-01-01")


def test_account_owns_read_only_timestamp_snapshot() -> None:
    caller_timestamps = np.array(["2026-01-01", "2026-01-02"], dtype="datetime64[D]")
    original_timestamps = caller_timestamps.copy()
    account = Account(
        [ContractGroup.get("account-timestamp-snapshot")],
        caller_timestamps,
        _price,
        SimpleNamespace(),
    )

    caller_timestamps[0] = np.datetime64("2030-01-01")

    assert np.array_equal(account.timestamps, original_timestamps)
    with pytest.raises(ValueError, match="read-only"):
        account.timestamps[0] = np.datetime64("2030-01-01")


def test_array_price_function_owns_read_only_input_snapshot() -> None:
    timestamps = np.array(["2026-01-01", "2026-01-02"], dtype="datetime64[D]")
    prices = np.array([100.0, 101.0])
    price_function = PriceFuncArrayDict({"SNAPSHOT": (timestamps, prices)})
    contract = Contract.create("SNAPSHOT")

    timestamps[0] = np.datetime64("2030-01-01")
    prices[0] = 999.0

    assert price_function(contract, np.array(["2026-01-01"], dtype="datetime64[D]"), 0, None) == 100.0
    stored_timestamps, stored_prices = price_function.price_dict["SNAPSHOT"]
    with pytest.raises(ValueError, match="read-only"):
        stored_timestamps[0] = np.datetime64("2030-01-01")
    with pytest.raises(ValueError, match="read-only"):
        stored_prices[0] = 999.0


def test_array_price_function_rejects_unsorted_timestamps() -> None:
    timestamps = np.array(["2026-01-02", "2026-01-01"], dtype="datetime64[D]")

    with pytest.raises(ValueError, match="strictly increasing and unique"):
        PriceFuncArrayDict({"UNSORTED": (timestamps, np.array([101.0, 100.0]))})


def test_dictionary_price_function_owns_nested_input_snapshot() -> None:
    timestamp = np.datetime64("2026-01-01")
    caller_prices = {"DICT-SNAPSHOT": {timestamp: 100.0}}
    price_function = PriceFuncDict(caller_prices)
    contract = Contract.create("DICT-SNAPSHOT")

    caller_prices["DICT-SNAPSHOT"][timestamp] = 999.0
    caller_prices["DICT-SNAPSHOT"][np.datetime64("2026-01-02")] = 101.0

    assert price_function(contract, np.array([timestamp]), 0, None) == 100.0
    assert len(price_function.price_dict["DICT-SNAPSHOT"]) == 1


@pytest.mark.parametrize("adapter", [VectorIndicator, VectorSignal])
def test_vector_stage_adapter_owns_read_only_input_snapshot(adapter) -> None:
    caller_values = np.array([1.0, 2.0])
    stage = adapter(caller_values)

    caller_values[0] = 999.0

    assert stage.vector.tolist() == [1.0, 2.0]
    with pytest.raises(ValueError, match="read-only"):
        stage.vector[0] = 999.0


def test_strategy_rejects_indicator_length_mismatch() -> None:
    timestamps = np.array(["2026-01-01", "2026-01-02"], dtype="datetime64[D]")
    group = ContractGroup.get("indicator-length-boundary")
    strategy = Strategy(timestamps, [group], _price)
    strategy.add_indicator("short", lambda *_args: np.array([1.0]))

    with pytest.raises(BacktestCallbackError, match="indicator callback 'short'.*indicator-length-boundary") as raised:
        strategy.run_indicators()

    assert isinstance(raised.value.__cause__, ValueError)
    assert "1 values for 2 strategy timestamps" in str(raised.value.__cause__)


def test_strategy_owns_read_only_indicator_output() -> None:
    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get("indicator-output-snapshot")
    callback_values = np.array([1.0])
    strategy = Strategy(np.array([timestamp]), [group], _price)
    strategy.add_indicator("snapshot", lambda *_args: callback_values)

    strategy.run_indicators()
    callback_values[0] = 999.0
    stored_values = strategy.indicator_values[group.name].snapshot

    assert stored_values[0] == 1.0
    with pytest.raises(ValueError, match="read-only"):
        stored_values[0] = 999.0


def test_strategy_rejects_signal_length_mismatch() -> None:
    timestamps = np.array(["2026-01-01", "2026-01-02"], dtype="datetime64[D]")
    group = ContractGroup.get("signal-length-boundary")
    strategy = Strategy(timestamps, [group], _price)
    strategy.add_signal("short", lambda *_args: np.array([True]))

    with pytest.raises(BacktestCallbackError, match="signal callback 'short'.*signal-length-boundary") as raised:
        strategy.run_signals()

    assert isinstance(raised.value.__cause__, ValueError)
    assert "1 values for 2 strategy timestamps" in str(raised.value.__cause__)


def test_strategy_preserves_indicator_callback_failure_cause() -> None:
    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get("indicator-failure-context")
    strategy = Strategy(np.array([timestamp]), [group], _price)

    def failed_indicator(*_args: object) -> np.ndarray:
        raise LookupError("missing input")

    strategy.add_indicator("failed", failed_indicator)

    with pytest.raises(BacktestCallbackError, match="indicator callback 'failed'.*indicator-failure-context") as raised:
        strategy.run_indicators()

    assert isinstance(raised.value.__cause__, LookupError)


@pytest.mark.parametrize("stage", ["indicator", "signal"])
def test_strategy_rejects_unconfigured_stage_group_atomically(stage: str) -> None:
    timestamp = np.datetime64("2026-01-01")
    configured_group = ContractGroup.get(f"configured-{stage}-scope")
    unconfigured_group = ContractGroup(f"configured-{stage}-scope")
    strategy = Strategy(np.array([timestamp]), [configured_group], _price)

    with pytest.raises(ValueError, match="not configured for this strategy"):
        if stage == "indicator":
            strategy.add_indicator("foreign", lambda *_args: np.array([1.0]), [unconfigured_group])
        else:
            strategy.add_signal("foreign", lambda *_args: np.array([True]), [unconfigured_group])

    assert "foreign" not in strategy.indicators
    assert "foreign" not in strategy.signals


def test_strategy_rejects_unconfigured_runtime_stage_group() -> None:
    timestamp = np.datetime64("2026-01-01")
    configured_group = ContractGroup.get("configured-runtime-stage-scope")
    unconfigured_group = ContractGroup("configured-runtime-stage-scope")
    strategy = Strategy(np.array([timestamp]), [configured_group], _price)
    strategy.add_indicator("value", lambda *_args: np.array([1.0]))

    with pytest.raises(ValueError, match="not configured for this strategy"):
        strategy.run_indicators(contract_groups=[unconfigured_group])

    assert not strategy.indicator_values


@pytest.mark.parametrize("stage", ["indicator", "signal"])
def test_strategy_rejects_duplicate_stage_registration_atomically(stage: str) -> None:
    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get(f"duplicate-{stage}-registration")
    strategy = Strategy(np.array([timestamp]), [group], _price)

    def original_callback(*_args: object) -> np.ndarray:
        return np.array([1.0])

    def replacement_callback(*_args: object) -> np.ndarray:
        return np.array([2.0])

    if stage == "indicator":
        strategy.add_indicator("duplicate", original_callback)
        with pytest.raises(ValueError, match="already registered"):
            strategy.add_indicator("duplicate", replacement_callback, depends_on=["missing"])
        assert strategy.indicators["duplicate"] is original_callback
        assert strategy.indicator_deps["duplicate"] == []
    else:
        strategy.add_signal("duplicate", original_callback)
        with pytest.raises(ValueError, match="already registered"):
            strategy.add_signal("duplicate", replacement_callback, depends_on_signals=["missing"])
        assert strategy.signals["duplicate"] is original_callback
        assert strategy.signal_deps["duplicate"] == []


@pytest.mark.parametrize("stage", ["indicator", "signal"])
def test_strategy_rejects_non_callable_stage_atomically(stage: str) -> None:
    timestamp = np.datetime64("2026-01-01")
    strategy = Strategy(
        np.array([timestamp]),
        [ContractGroup.get(f"non-callable-{stage}")],
        _price,
    )

    with pytest.raises(TypeError, match="must be callable"):
        if stage == "indicator":
            strategy.add_indicator("invalid", 42)  # type: ignore[arg-type]
        else:
            strategy.add_signal("invalid", 42)  # type: ignore[arg-type]

    assert "invalid" not in strategy.indicators
    assert "invalid" not in strategy.signals


def test_strategy_dispatches_same_indicator_name_by_contract_group() -> None:
    timestamp = np.datetime64("2026-01-01")
    first_group = ContractGroup.get("first-indicator-dispatch")
    second_group = ContractGroup.get("second-indicator-dispatch")
    strategy = Strategy(np.array([timestamp]), [first_group, second_group], _price)
    strategy.add_indicator("price", lambda *_args: np.array([100.0]), [first_group])
    strategy.add_indicator("price", lambda *_args: np.array([200.0]), [second_group])

    strategy.run_indicators()

    assert strategy.indicator_values[first_group.name].price.tolist() == [100.0]
    assert strategy.indicator_values[second_group.name].price.tolist() == [200.0]


def test_strategy_dispatches_same_signal_name_by_contract_group() -> None:
    timestamp = np.datetime64("2026-01-01")
    first_group = ContractGroup.get("first-signal-dispatch")
    second_group = ContractGroup.get("second-signal-dispatch")
    strategy = Strategy(np.array([timestamp]), [first_group, second_group], _price)
    strategy.add_signal("entry", lambda *_args: np.array([True]), [first_group])
    strategy.add_signal("entry", lambda *_args: np.array([False]), [second_group])

    strategy.run_signals()

    assert strategy.signal_values[first_group.name].entry.tolist() == [True]
    assert strategy.signal_values[second_group.name].entry.tolist() == [False]


def test_date_filtered_rule_execution_does_not_mutate_signal_values() -> None:
    timestamps = np.array(["2026-01-01", "2026-01-02"], dtype="datetime64[D]")
    group = ContractGroup.get("date-filtered-rule-signals")
    strategy = Strategy(timestamps, [group], _price)
    callback_timestamps: list[np.datetime64] = []
    strategy.add_signal("entry", lambda *_args: np.array([True, True]))

    def record_rule(
        _group: ContractGroup,
        index: int,
        stage_timestamps: np.ndarray,
        *_args: object,
    ) -> list[MarketOrder]:
        callback_timestamps.append(stage_timestamps[index])
        return []

    strategy.add_rule("record", record_rule, "entry")
    strategy.run_signals()
    original_values = strategy.signal_values[group.name].entry.copy()

    strategy.run_rules(start_date=timestamps[1])

    assert callback_timestamps == [timestamps[1]]
    assert np.array_equal(strategy.signal_values[group.name].entry, original_values)


@pytest.mark.parametrize("stage", ["indicator", "signal", "rule"])
def test_strategy_rejects_unknown_selective_stage_name(stage: str) -> None:
    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get(f"unknown-selective-{stage}")
    strategy = Strategy(np.array([timestamp]), [group], _price)

    with pytest.raises(ValueError, match=f"unknown {stage} names: missing"):
        if stage == "indicator":
            strategy.run_indicators(["missing"])
        elif stage == "signal":
            strategy.run_signals(["missing"])
        else:
            strategy.run_rules(["missing"])


def test_strategy_rejects_unconfigured_rule_execution_group() -> None:
    timestamp = np.datetime64("2026-01-01")
    configured_group = ContractGroup.get("configured-rule-execution")
    unconfigured_group = ContractGroup("configured-rule-execution")
    strategy = Strategy(np.array([timestamp]), [configured_group], _price)

    with pytest.raises(ValueError, match="not configured for this strategy"):
        strategy.run_rules(contract_groups=[unconfigured_group])


def test_strategy_rejects_invalid_rule_filter_atomically() -> None:
    timestamp = np.datetime64("2026-01-01")
    strategy = Strategy(
        np.array([timestamp]),
        [ContractGroup.get("invalid-rule-filter")],
        _price,
    )

    with pytest.raises(ValueError, match="invalid rule position_filter"):
        strategy.add_rule("invalid", lambda *_args: [], "entry", position_filter="sideways")

    assert "invalid" not in strategy.rule_names
    assert "invalid" not in strategy.rules
    assert "invalid" not in strategy.rule_signals
    assert "invalid" not in strategy.position_filters


def test_strategy_owns_rule_trigger_value_snapshot() -> None:
    timestamp = np.datetime64("2026-01-01")
    strategy = Strategy(
        np.array([timestamp]),
        [ContractGroup.get("rule-trigger-snapshot")],
        _price,
    )
    trigger_values = [1]

    strategy.add_rule("entry", lambda *_args: [], "signal", trigger_values)
    trigger_values.append(2)

    assert strategy.rule_signals["entry"] == ("signal", (1,))


def test_strategy_rejects_duplicate_rule_atomically() -> None:
    timestamp = np.datetime64("2026-01-01")
    strategy = Strategy(
        np.array([timestamp]),
        [ContractGroup.get("duplicate-rule-registration")],
        _price,
    )

    def original_rule(*_args: object) -> list[MarketOrder]:
        return []

    strategy.add_rule("entry", original_rule, "signal")

    with pytest.raises(ValueError, match="already registered"):
        strategy.add_rule("entry", lambda *_args: [], "replacement")

    assert strategy.rules["entry"] is original_rule
    assert strategy.rule_signals["entry"] == ("signal", (True,))


def test_strategy_rejects_non_callable_market_simulator_atomically() -> None:
    timestamp = np.datetime64("2026-01-01")
    strategy = Strategy(
        np.array([timestamp]),
        [ContractGroup.get("non-callable-market-simulator")],
        _price,
    )

    with pytest.raises(TypeError, match="market simulator must be callable"):
        strategy.add_market_sim(42)  # type: ignore[arg-type]

    assert strategy.market_sims == []


@pytest.mark.parametrize(
    "policy",
    [
        SimpleNamespace(name="invalid"),
        SimpleNamespace(name="", evaluate=lambda *_args: None),
    ],
)
def test_strategy_rejects_invalid_risk_policy_atomically(policy: object) -> None:
    timestamp = np.datetime64("2026-01-01")
    strategy = Strategy(
        np.array([timestamp]),
        [ContractGroup.get(f"invalid-risk-policy-{id(policy)}")],
        _price,
    )

    with pytest.raises(TypeError, match="risk policy must expose"):
        strategy.add_risk_policy(policy)  # type: ignore[arg-type]

    assert strategy.risk_policies == []


def test_account_rejects_non_callable_price_function() -> None:
    timestamps = np.array(["2026-01-01"], dtype="datetime64[D]")

    with pytest.raises(TypeError, match="price_function must be callable"):
        Account([ContractGroup.get("invalid-price-function")], timestamps, 100.0, SimpleNamespace())


def test_account_rejects_off_grid_valuation_without_leaking_index_error() -> None:
    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get("off-grid-account")
    contract = Contract.create("OFF-GRID", group)
    account = Account([group], np.array([timestamp]), _price, SimpleNamespace())
    order = MarketOrder(contract=contract, timestamp=timestamp, qty=1)
    account.add_trades([Trade(contract, order, timestamp, 1, 100.0)])

    with pytest.raises(ValueError, match="not present in the account timestamp grid"):
        account.calc(timestamp + np.timedelta64(1, "D"))


def test_empty_account_rejects_off_grid_valuation() -> None:
    timestamp = np.datetime64("2026-01-01")
    account = Account(
        [ContractGroup.get("empty-off-grid-account")],
        np.array([timestamp]),
        _price,
        SimpleNamespace(),
    )

    with pytest.raises(ValueError, match="not present in the account timestamp grid"):
        account.equity(timestamp + np.timedelta64(1, "D"))

    assert not account._pnl


def test_account_rejects_invalid_trade_batch_before_mutation() -> None:
    timestamps = np.array(["2026-01-01", "2026-01-02"], dtype="datetime64[D]")
    group = ContractGroup.get("atomic-trade-batch")
    contract = Contract.create("ATOMIC-TRADE", group)
    account = Account([group], timestamps, _price, SimpleNamespace())
    valid_order = MarketOrder(contract=contract, timestamp=timestamps[0], qty=1)
    off_grid_timestamp = timestamps[-1] + np.timedelta64(1, "D")
    invalid_order = MarketOrder(contract=contract, timestamp=off_grid_timestamp, qty=1)

    with pytest.raises(ValueError, match="not present in the account timestamp grid"):
        account.add_trades(
            [
                Trade(contract, valid_order, timestamps[0], 1, 100.0),
                Trade(contract, invalid_order, off_grid_timestamp, 1, 100.0),
            ]
        )

    assert account.trades() == []
    assert account.symbols() == []


def test_account_owns_and_returns_detached_trade_snapshots() -> None:
    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get("account-trade-snapshot")
    contract = Contract.create("ACCOUNT-TRADE-SNAPSHOT", group)
    account = Account([group], np.array([timestamp]), _price, SimpleNamespace())
    order = MarketOrder(contract=contract, timestamp=timestamp, qty=1)
    source_trade = Trade(contract, order, timestamp, 1, 100.0, properties=SimpleNamespace(source="original"))

    account.add_trades([source_trade])
    source_trade.qty = 99
    source_trade.price = 999.0
    source_trade.properties.source = "mutated"
    returned_trade = account.trades()[0]

    assert returned_trade.qty == 1
    assert returned_trade.price == 100.0
    assert returned_trade.properties.source == "original"

    returned_trade.qty = 50
    returned_trade.properties.source = "returned mutation"

    stable_trade = account.trades()[0]
    assert stable_trade.qty == 1
    assert stable_trade.properties.source == "original"
    assert account.trade_count == 1
    assert account.position(group, timestamp) == 1


def test_trade_rejects_contract_mismatch_at_construction() -> None:
    timestamp = np.datetime64("2026-01-01")
    first = Contract.create("TRADE-CONTRACT-FIRST")
    second = Contract.create("TRADE-CONTRACT-SECOND")
    order = MarketOrder(contract=first, timestamp=timestamp, qty=1)

    with pytest.raises(ValueError, match="must match its originating order"):
        Trade(second, order, timestamp, 1, 100.0)


def test_trade_rejects_execution_before_order() -> None:
    order_timestamp = np.datetime64("2026-01-02")
    contract = Contract.create("TRADE-CAUSALITY")
    order = MarketOrder(contract=contract, timestamp=order_timestamp, qty=1)

    with pytest.raises(ValueError, match="cannot precede"):
        Trade(contract, order, order_timestamp - np.timedelta64(1, "D"), 1, 100.0)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("price", np.inf),
        ("fee", np.nan),
        ("commission", True),
    ],
)
def test_trade_rejects_invalid_financial_scalar(field: str, value: object) -> None:
    timestamp = np.datetime64("2026-01-01")
    contract = Contract.create(f"INVALID-TRADE-{field}")
    order = MarketOrder(contract=contract, timestamp=timestamp, qty=1)
    kwargs = {"price": 100.0, "fee": 0.0, "commission": 0.0, field: value}

    with pytest.raises(ValueError, match=f"trade {field} must be a finite real number"):
        Trade(contract, order, timestamp, 1, **kwargs)


def test_account_rejects_trade_outside_configured_contract_groups() -> None:
    timestamp = np.datetime64("2026-01-01")
    configured_group = ContractGroup.get("configured-account-group")
    outside_group = ContractGroup.get("outside-account-group")
    contract = Contract.create("OUTSIDE-ACCOUNT", outside_group)
    order = MarketOrder(contract=contract, timestamp=timestamp, qty=1)
    account = Account([configured_group], np.array([timestamp]), _price, SimpleNamespace())

    with pytest.raises(ValueError, match="outside the account's configured contract groups"):
        account.add_trades([Trade(contract, order, timestamp, 1, 100.0)])

    assert account.symbols() == []


def test_account_rejects_retroactive_cross_contract_batch_before_mutation() -> None:
    timestamps = np.array(["2026-01-01", "2026-01-02", "2026-01-03"], dtype="datetime64[D]")
    group = ContractGroup.get("chronological-trade-batch")
    first = Contract.create("CHRONO-FIRST", group)
    second = Contract.create("CHRONO-SECOND", group)
    account = Account([group], timestamps, _price, SimpleNamespace())

    def trade(contract: Contract, timestamp: np.datetime64) -> Trade:
        order = MarketOrder(contract=contract, timestamp=timestamp, qty=1)
        return Trade(contract, order, timestamp, 1, 100.0)

    account.add_trades([trade(first, timestamps[1]), trade(second, timestamps[1])])

    with pytest.raises(ValueError, match="non-decreasing timestamps"):
        account.add_trades([trade(first, timestamps[2]), trade(second, timestamps[0])])

    assert account.position(group, timestamps[2]) == 2
    assert len(account.trades()) == 2


def test_account_rolls_back_cross_contract_batch_when_price_callback_fails() -> None:
    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get("callback-rollback")
    first = Contract.create("ROLLBACK-FIRST", group)
    second = Contract.create("ROLLBACK-SECOND", group)
    reject_second = False

    def price(contract, *_args):
        if reject_second and contract is second:
            raise RuntimeError("price unavailable")
        return 110.0

    def trade(contract: Contract) -> Trade:
        order = MarketOrder(contract=contract, timestamp=timestamp, qty=1)
        return Trade(contract, order, timestamp, 1, 100.0)

    account = Account([group], np.array([timestamp]), price, SimpleNamespace(), starting_equity=1_000.0)
    account.add_trades([trade(first), trade(second)])
    assert account.equity(timestamp) == 1_020.0

    reject_second = True
    with pytest.raises(RuntimeError, match="price unavailable"):
        account.add_trades([trade(first), trade(second)])

    assert account.position(group, timestamp) == 2
    assert len(account.trades()) == 2
    assert account.equity(timestamp) == 1_020.0


@pytest.mark.parametrize(
    ("value", "error"),
    [(-1, ValueError), (1440, ValueError), (900.5, TypeError), (True, TypeError)],
)
def test_account_rejects_invalid_daily_calculation_time(value, error) -> None:
    timestamps = np.array(["2026-01-01"], dtype="datetime64[D]")

    with pytest.raises(error, match="pnl_calc_time"):
        Account([ContractGroup.get("invalid-calc-time")], timestamps, _price, SimpleNamespace(), pnl_calc_time=value)


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), "100", [100.0], True])
def test_market_simulator_rejects_invalid_price_callback_values(value) -> None:
    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get("price-boundary")
    contract = Contract.create("PRICE-BOUNDARY", group)
    order = MarketOrder(contract=contract, timestamp=timestamp, qty=1.0)
    simulator = SimpleMarketSimulator(lambda *_args: value)

    with pytest.raises((TypeError, ValueError), match="price"):
        simulator([order], 0, np.array([timestamp]), {}, {}, SimpleNamespace())


def test_missing_mark_carries_unrealized_pnl_without_nan_contamination() -> None:
    timestamps = np.array(["2026-01-01", "2026-01-02"], dtype="datetime64[D]")
    group = ContractGroup.get("missing-mark")
    contract = Contract.create("MISSING-MARK", group)

    def price(_contract, _timestamps, index, _context):
        return 100.0 if index == 0 else float("nan")

    account = Account([group], timestamps, price, SimpleNamespace(), starting_equity=1_000.0)
    order = MarketOrder(contract=contract, timestamp=timestamps[0], qty=1.0)
    account.add_trades([Trade(contract, order, timestamps[0], 1.0, 100.0)])

    account.calc(timestamps[1])

    assert np.isfinite(account.equity(timestamps[1]))
    assert account.equity(timestamps[1]) == 1_000.0


def test_rule_contract_violation_is_chained_with_context() -> None:
    timestamp = np.datetime64("2026-01-01")
    expected_group = ContractGroup.get("expected-rule-group")
    other_group = ContractGroup.get("other-rule-group")
    wrong_contract = Contract.create("WRONG-GROUP", other_group)
    strategy = Strategy(np.array([timestamp]), [expected_group], _price)
    strategy.position_filters["bad-rule"] = None

    def bad_rule(*_args):
        return [MarketOrder(contract=wrong_contract, timestamp=timestamp, qty=1.0)]

    with pytest.raises(BacktestCallbackError, match="expected-rule-group") as raised:
        strategy._get_orders(
            0,
            bad_rule,
            expected_group,
            {"indicator_values": SimpleNamespace(), "signal_values": np.array([True]), "rule_name": "bad-rule"},
        )

    assert isinstance(raised.value.__cause__, ValueError)


def test_rule_callback_cannot_mutate_current_order_membership() -> None:
    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get("immutable-current-orders")
    contract = Contract.create("IMMUTABLE-CURRENT-ORDERS", group)
    strategy = Strategy(np.array([timestamp]), [group], _price)
    existing_order = MarketOrder(contract=contract, timestamp=timestamp, qty=1)
    strategy._current_orders = [existing_order]
    strategy.position_filters["mutating-rule"] = None

    def mutating_rule(_group, _index, _timestamps, _indicators, _signals, _account, current_orders, _context):
        current_orders.append(MarketOrder(contract=contract, timestamp=timestamp, qty=1))
        return []

    with pytest.raises(BacktestCallbackError, match="rule callback") as raised:
        strategy._get_orders(
            0,
            mutating_rule,
            group,
            {
                "indicator_values": SimpleNamespace(),
                "signal_values": np.array([True]),
                "rule_name": "mutating-rule",
            },
        )

    assert isinstance(raised.value.__cause__, AttributeError)
    assert strategy._current_orders == [existing_order]


def test_invalid_market_simulator_output_is_chained_before_account_mutation() -> None:
    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get("sim-boundary")
    contract = Contract.create("SIM-BOUNDARY", group)
    strategy = Strategy(np.array([timestamp]), [group], _price)
    order = MarketOrder(contract=contract, timestamp=timestamp, qty=1.0)
    strategy._current_orders = [order]
    strategy.market_sims = [lambda *_args: [object()]]

    with pytest.raises(BacktestCallbackError, match="market simulator") as raised:
        strategy._sim_market(0)

    assert isinstance(raised.value.__cause__, TypeError)
    assert strategy.trades() == []


def test_invalid_market_simulator_output_restores_order_lifecycle_state() -> None:
    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get("sim-order-rollback")
    contract = Contract.create("SIM-ORDER-ROLLBACK", group)
    strategy = Strategy(np.array([timestamp]), [group], _price)
    order = MarketOrder(contract=contract, timestamp=timestamp, qty=2)
    strategy._current_orders = [order]

    def invalid_simulator(current_orders, *_args):
        current_orders[0].fill(1)
        return [object()]

    strategy.market_sims = [invalid_simulator]

    with pytest.raises(BacktestCallbackError, match="market simulator"):
        strategy._sim_market(0)

    assert order.qty == 2
    assert order.status is OrderStatus.OPEN
    assert strategy._current_orders == [order]


def test_market_simulator_interruption_restores_order_state_without_wrapping() -> None:
    class ExecutionInterrupted(BaseException):
        pass

    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get("sim-order-interruption")
    contract = Contract.create("SIM-ORDER-INTERRUPTION", group)
    strategy = Strategy(np.array([timestamp]), [group], _price)
    order = MarketOrder(contract=contract, timestamp=timestamp, qty=2)
    strategy._current_orders = [order]

    def interrupted_simulator(current_orders, *_args):
        current_orders[0].fill(1)
        raise ExecutionInterrupted

    strategy.market_sims = [interrupted_simulator]

    with pytest.raises(ExecutionInterrupted):
        strategy._sim_market(0)

    assert order.qty == 2
    assert order.status is OrderStatus.OPEN


def test_callback_contracts_normalize_rule_orders_without_strategy_state() -> None:
    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get("pure-rule-contract")
    order = MarketOrder(contract=Contract.create("PURE-RULE", group), timestamp=timestamp, qty=1.0)

    result = validate_rule_orders((order,), group, timestamp)

    assert result == [order]


def test_callback_contracts_reject_order_for_wrong_strategy_timestamp() -> None:
    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get("wrong-rule-timestamp")
    contract = Contract.create("WRONG-RULE-TIMESTAMP", group)
    order = MarketOrder(contract=contract, timestamp=timestamp + np.timedelta64(1, "D"), qty=1)

    with pytest.raises(ValueError, match="does not match the current strategy timestamp"):
        validate_rule_orders([order], group, timestamp)


def test_callback_contracts_reject_trade_for_unknown_order_without_account_mutation() -> None:
    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get("pure-trade-contract")
    contract = Contract.create("PURE-TRADE", group)
    open_order = MarketOrder(contract=contract, timestamp=timestamp, qty=1.0)
    unknown_order = MarketOrder(contract=contract, timestamp=timestamp, qty=1.0)
    trade = Trade(contract, unknown_order, timestamp, 1.0, 100.0)

    with pytest.raises(ValueError, match="outside the open order set"):
        validate_market_trades(
            [trade],
            [open_order],
            timestamp,
            {id(open_order): (open_order.qty, open_order.status)},
        )


def test_callback_contracts_normalize_market_trades_to_detached_list() -> None:
    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get("normalized-market-trades")
    contract = Contract.create("NORMALIZED-MARKET-TRADES", group)
    order = MarketOrder(contract=contract, timestamp=timestamp, qty=1)
    trade = Trade(contract, order, timestamp, 1, 100.0)
    callback_result = (trade,)
    original_quantity = order.qty
    order.fill(1)

    result = validate_market_trades(
        callback_result,
        [order],
        timestamp,
        {id(order): (original_quantity, OrderStatus.OPEN)},
    )

    assert result == [trade]
    assert isinstance(result, list)


def test_strategy_applies_reported_trade_when_simulator_does_not_mutate_order() -> None:
    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get("unapplied-simulator-fill")
    contract = Contract.create("UNAPPLIED-SIMULATOR-FILL", group)
    strategy = Strategy(np.array([timestamp]), [group], _price)
    order = MarketOrder(contract=contract, timestamp=timestamp, qty=1)
    strategy._current_orders = [order]
    strategy.market_sims = [lambda *_args: [Trade(contract, order, timestamp, 1, 100.0)]]

    strategy._sim_market(0)

    assert order.qty == 0
    assert order.status is OrderStatus.FILLED
    assert len(strategy.trades()) == 1


def test_strategy_rejects_simulator_fill_with_incoherent_order_status() -> None:
    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get("incoherent-simulator-fill")
    contract = Contract.create("INCOHERENT-SIMULATOR-FILL", group)
    strategy = Strategy(np.array([timestamp]), [group], _price)
    order = MarketOrder(contract=contract, timestamp=timestamp, qty=1)
    strategy._current_orders = [order]

    def incoherent_simulator(*_args: object) -> list[Trade]:
        order.qty = 0
        return [Trade(contract, order, timestamp, 1, 100.0)]

    strategy.market_sims = [incoherent_simulator]

    with pytest.raises(BacktestCallbackError, match="market simulator failed"):
        strategy._sim_market(0)

    assert order.qty == 1
    assert order.status is OrderStatus.OPEN
    assert strategy.trades() == []


def test_strategy_rejects_partial_status_without_reported_fill() -> None:
    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get("unreported-simulator-fill")
    contract = Contract.create("UNREPORTED-SIMULATOR-FILL", group)
    strategy = Strategy(np.array([timestamp]), [group], _price)
    order = MarketOrder(contract=contract, timestamp=timestamp, qty=2)
    strategy._current_orders = [order]

    def incoherent_simulator(*_args: object) -> list[Trade]:
        order.status = OrderStatus.PARTIALLY_FILLED
        return []

    strategy.market_sims = [incoherent_simulator]

    with pytest.raises(BacktestCallbackError, match="market simulator failed"):
        strategy._sim_market(0)

    assert order.qty == 2
    assert order.status is OrderStatus.OPEN
