from __future__ import annotations

import gambit


def test_root_api_is_explicit_and_free_of_dependency_leaks() -> None:
    expected = {
        "Account",
        "BacktestResult",
        "Contract",
        "ContractGroup",
        "MarketOrder",
        "Strategy",
        "StrategyBuilder",
        "has_display",
        "validate_market_data",
    }

    assert expected <= set(gambit.__all__)
    assert not {"Any", "Callable", "SimpleNamespace", "np", "pl", "math", "dataclass", "foo"} & set(gambit.__all__)
    assert all(not name.startswith("test_") for name in gambit.__all__)
    assert sorted(gambit.__all__) == sorted(set(gambit.__all__))


def test_option_rho_is_bound_to_rho_formula() -> None:
    value = gambit.rho(True, 100.0, 100.0, 1.0, 0.05, 0.2)

    assert 0.5 < value < 0.6
