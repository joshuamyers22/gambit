from __future__ import annotations

import datetime

import numpy as np
import pytest

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
