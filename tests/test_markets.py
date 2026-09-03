from __future__ import annotations

import datetime

import numpy as np
import pytest

from gambit.holiday_calendars import get_date_from_weekday
from gambit.markets import EminiFuture, EminiOption


def test_current_decade_future_uses_standard_one_digit_year() -> None:
    assert EminiFuture.get_expiry("ESZ6") == np.datetime64("2026-12-18T08:30")


def test_historical_future_requires_and_decodes_two_digit_year() -> None:
    assert EminiFuture.get_expiry("ESZ16") == np.datetime64("2016-12-16T08:30")
    assert EminiFuture.get_current_symbol(datetime.date(2019, 3, 14)) == "ESH19"


def test_symbol_navigation_preserves_deterministic_decade() -> None:
    assert EminiFuture.get_previous_symbol("ESH0") == "ESZ19"
    assert EminiFuture.get_next_symbol("ESZ9") == "ESH30"
    assert EminiFuture.get_previous_symbol("ESH19") == "ESZ18"


@pytest.mark.parametrize("symbol", ["ESH", "ESH2018", "ESH-8", "ESY6", "NQZ6"])
def test_future_symbol_rejects_unsupported_or_malformed_names(symbol: str) -> None:
    with pytest.raises(ValueError, match="invalid E-mini future symbol"):
        EminiFuture.get_expiry(symbol)


def test_option_year_policy_distinguishes_historical_and_current_decades() -> None:
    assert EminiOption.decode_symbol("EW2Z15")[1] == 2015
    assert EminiOption.decode_symbol("EW2Z5")[1] == 2025
    assert EminiOption.get_expiry("EW2Z15") == np.datetime64("2015-12-11T15:00")


@pytest.mark.parametrize("symbol", ["EW2Y5", "EW6Z5", "E0AF5", "EWZ2015", "junk"])
def test_option_symbol_requires_supported_exact_shape(symbol: str) -> None:
    with pytest.raises(Exception, match="could not decode"):
        EminiOption.decode_symbol(symbol)


def test_weekday_occurrence_cannot_drift_into_another_month() -> None:
    assert get_date_from_weekday(0, 2021, 3, 5) == np.datetime64("2021-03-29")
    with pytest.raises(ValueError, match="does not occur 5 times"):
        get_date_from_weekday(0, 2021, 2, 5)
    with pytest.raises(ValueError, match="does not occur 5 times"):
        EminiOption.get_expiry("E5AG21")


@pytest.mark.parametrize(("weekday", "week"), [(-1, 1), (7, 1), (0, 0), (0, 6), (0, -2)])
def test_weekday_occurrence_rejects_invalid_coordinates(weekday: int, week: int) -> None:
    with pytest.raises(ValueError):
        get_date_from_weekday(weekday, 2026, 1, week)


def test_end_of_month_option_keeps_explicit_calendar_month_semantics() -> None:
    assert get_date_from_weekday(2, 2021, 2, -1) == np.datetime64("2021-02-28")
    assert EminiOption.get_expiry("EWG21") == np.datetime64("2021-02-26T15:00")
