from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from gambit.factor_identity import FactorColumnSchema, FactorNodeIdentity
from gambit.market_data import (
    MarketDataAvailabilityPolicy,
    PointInTimeMarketData,
    PointInTimePriceFunction,
)
from gambit.pq_types import Contract, ContractGroup
from gambit.strategy import Strategy

pytestmark = pytest.mark.acceptance


def _frame(*, future_value: float = 103.0) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["A", "A", "A", "A"],
            "field": ["price", "price", "price", "price"],
            "observation_time": np.array(
                [
                    "2024-01-02T09:30",
                    "2024-01-02T09:30",
                    "2024-01-02T09:31",
                    "2024-01-02T09:33",
                ],
                dtype="datetime64[ns]",
            ),
            "available_time": np.array(
                [
                    "2024-01-02T09:30",
                    "2024-01-02T09:32",
                    "2024-01-02T09:32",
                    "2024-01-02T09:33",
                ],
                dtype="datetime64[ns]",
            ),
            "revision": ["initial", "corrected", "initial", "initial"],
            "value": [100.0, 101.0, 102.0, future_value],
        }
    )


def _data(*, future_value: float = 103.0, dataset_revision: str = "snapshot-1") -> PointInTimeMarketData:
    return PointInTimeMarketData(
        _frame(future_value=future_value),
        source="vendor-bars",
        dataset_revision=dataset_revision,
    )


def test_scalar_read_uses_only_revision_available_at_heartbeat() -> None:
    data = _data()
    observation_time = np.datetime64("2024-01-02T09:30")

    initial = data.read(
        "A",
        "price",
        observation_time,
        as_of=np.datetime64("2024-01-02T09:31"),
    )
    corrected = data.read(
        "A",
        "price",
        observation_time,
        as_of=np.datetime64("2024-01-02T09:32"),
    )

    assert initial is not None and (initial.value, initial.revision) == (100.0, "initial")
    assert corrected is not None and (corrected.value, corrected.revision) == (101.0, "corrected")
    assert corrected.available_time == np.datetime64("2024-01-02T09:32", "ns")


def test_publication_at_same_heartbeat_is_available_inclusively() -> None:
    observation = _data().read(
        "A",
        "price",
        np.datetime64("2024-01-02T09:30", "m"),
        as_of=np.datetime64("2024-01-02T09:30:00", "s"),
    )

    assert observation is not None and observation.value == 100.0


def test_future_values_and_later_dataset_revision_cannot_change_earlier_read() -> None:
    first = _data(future_value=103.0, dataset_revision="snapshot-1")
    later = _data(future_value=999.0, dataset_revision="snapshot-2")
    requested = np.datetime64("2024-01-02T09:30")
    heartbeat = np.datetime64("2024-01-02T09:31")

    first_value = first.read("A", "price", requested, as_of=heartbeat)
    later_value = later.read("A", "price", requested, as_of=heartbeat)

    assert first_value is not None and later_value is not None
    assert first_value.value == later_value.value == 100.0
    assert first.fingerprint != later.fingerprint


def test_dataset_owns_a_snapshot_of_constructor_rows() -> None:
    frame = _frame()
    data = PointInTimeMarketData(frame, source="vendor-bars", dataset_revision="snapshot-1")
    frame[0, "value"] = 999.0

    observation = data.read(
        "A",
        "price",
        np.datetime64("2024-01-02T09:30"),
        as_of=np.datetime64("2024-01-02T09:30"),
    )

    assert observation is not None and observation.value == 100.0


def test_delayed_publication_is_unavailable_until_release() -> None:
    data = _data()
    observation_time = np.datetime64("2024-01-02T09:31")

    with pytest.raises(LookupError, match="no price observation"):
        data.read("A", "price", observation_time, as_of=observation_time)

    released = data.read(
        "A",
        "price",
        observation_time,
        as_of=np.datetime64("2024-01-02T09:32"),
    )
    assert released is not None and released.value == 102.0


def test_future_observation_request_is_rejected() -> None:
    with pytest.raises(ValueError, match="after the as_of heartbeat"):
        _data().read(
            "A",
            "price",
            np.datetime64("2024-01-02T09:32"),
            as_of=np.datetime64("2024-01-02T09:31"),
        )


def test_availability_policies_require_enum_members_even_when_data_exists() -> None:
    with pytest.raises(TypeError, match="MarketDataAvailabilityPolicy"):
        _data().read(
            "A",
            "price",
            np.datetime64("2024-01-02T09:30"),
            as_of=np.datetime64("2024-01-02T09:30"),
            missing_policy="error",  # type: ignore[arg-type]
        )


def test_window_returns_latest_causally_available_revision_per_observation() -> None:
    data = _data()
    start = np.datetime64("2024-01-02T09:30")
    end = np.datetime64("2024-01-02T09:31")

    before_release = data.read_window(
        "A", "price", start, start, as_of=np.datetime64("2024-01-02T09:31")
    )
    after_release = data.read_window(
        "A", "price", start, end, as_of=np.datetime64("2024-01-02T09:32")
    )

    assert before_release["value"].to_list() == [100.0]
    assert after_release["value"].to_list() == [101.0, 102.0]
    assert after_release["revision"].to_list() == ["corrected", "initial"]


@pytest.mark.parametrize(
    "policy", [MarketDataAvailabilityPolicy.SKIP, MarketDataAvailabilityPolicy.WARN]
)
def test_missing_policy_can_return_an_explicit_missing_value(
    policy: MarketDataAvailabilityPolicy,
) -> None:
    data = _data()
    kwargs = {
        "symbol": "MISSING",
        "field_name": "price",
        "observation_time": np.datetime64("2024-01-02T09:30"),
        "as_of": np.datetime64("2024-01-02T09:30"),
        "missing_policy": policy,
    }

    if policy is MarketDataAvailabilityPolicy.WARN:
        with pytest.warns(RuntimeWarning, match="no price observation"):
            assert data.read(**kwargs) is None
    else:
        assert data.read(**kwargs) is None


def test_previous_value_respects_maximum_age_and_stale_policy() -> None:
    data = _data()
    heartbeat = np.datetime64("2024-01-02T09:32")

    with pytest.raises(LookupError, match="is stale"):
        data.read(
            "A",
            "price",
            heartbeat,
            as_of=heartbeat,
            allow_previous=True,
            max_age=np.timedelta64(30, "s"),
        )
    assert (
        data.read(
            "A",
            "price",
            heartbeat,
            as_of=heartbeat,
            allow_previous=True,
            max_age=np.timedelta64(30, "s"),
            stale_policy=MarketDataAvailabilityPolicy.SKIP,
        )
        is None
    )


def test_point_in_time_price_adapter_obeys_release_time_and_records_provenance() -> None:
    data = _data()
    adapter = PointInTimePriceFunction(data, allow_previous=True, max_age=np.timedelta64(2, "m"))
    group = ContractGroup.get("point-in-time-prices")
    contract = Contract.create("A", group)
    timestamps = np.array(
        ["2024-01-02T09:30", "2024-01-02T09:31", "2024-01-02T09:32"],
        dtype="datetime64[ns]",
    )

    assert [adapter(contract, timestamps, index, None) for index in range(3)] == [100.0, 100.0, 102.0]
    strategy = Strategy(timestamps, [group], adapter)
    result = strategy.run()
    assert result.provenance.input_fingerprints == {"market_data": data.fingerprint}


def test_point_in_time_price_adapter_values_basket_components_causally() -> None:
    first_symbol = "POINT-IN-TIME-BASKET-A"
    second_symbol = "POINT-IN-TIME-BASKET-B"
    frame = pl.concat(
        [
            _frame().with_columns(pl.lit(first_symbol).alias("symbol")),
            _frame().with_columns(
                pl.lit(second_symbol).alias("symbol"),
                (pl.col("value") * 2.0).alias("value"),
            ),
        ]
    )
    data = PointInTimeMarketData(frame, source="vendor-bars", dataset_revision="snapshot-1")
    adapter = PointInTimePriceFunction(data)
    first = Contract.create(first_symbol)
    second = Contract.create(second_symbol)
    basket = Contract.create(
        "POINT-IN-TIME-BASKET",
        components=[(first, 2.0), (second, -0.5)],
    )
    timestamps = np.array(["2024-01-02T09:30"], dtype="datetime64[ns]")

    assert adapter(basket, timestamps, 0, None) == 100.0


def test_dataset_revision_invalidates_factor_identity() -> None:
    first = _data(dataset_revision="snapshot-1")
    second = _data(dataset_revision="snapshot-2")

    def identity(data: PointInTimeMarketData) -> FactorNodeIdentity:
        return FactorNodeIdentity(
            transform="research.return",
            transform_version="1",
            input_fingerprints={"prices": data.fingerprint},
            output_schema=(FactorColumnSchema("return", "float64", nullable=True),),
            row_ordering=("observation_time",),
        )

    assert identity(first).node_key != identity(second).node_key


@pytest.mark.parametrize(
    ("frame", "message"),
    [
        (_frame().drop("revision"), "must contain exactly"),
        (pl.concat([_frame().head(1), _frame().head(1)]), "availability identity"),
        (
            _frame().with_columns(pl.col("observation_time").alias("available_time"))
            .with_columns(pl.col("available_time") - pl.duration(minutes=1)),
            "cannot precede observation",
        ),
        (_frame().with_columns(pl.lit(float("nan")).alias("value")), "must be finite"),
        (
            _frame().with_columns(
                pl.col("observation_time").dt.replace_time_zone("UTC")
            ),
            "timezone-naive",
        ),
    ],
)
def test_point_in_time_dataset_rejects_ambiguous_rows(frame: pl.DataFrame, message: str) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        PointInTimeMarketData(frame, source="vendor-bars", dataset_revision="snapshot-1")
