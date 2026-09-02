# Gambit architecture

Gambit is a feature-oriented numerical library. Its dependency direction is
incrementally constrained rather than represented by framework-style layer
directories. New code should point from volatile edges toward stable contracts:

```text
CLI / notebooks / plotting / persistence
                  |
                  v
strategy and factor orchestration
                  |
                  v
stable contract kernel
```

## Stable contract kernel

The following modules define small, deterministic contracts and must not import
other `gambit` modules:

- `boundaries`
- `calculation`
- `factor_identity`
- `instruments`
- `market_data`

This makes their validation, identities, and value policies usable without
files, processes, plotting packages, cache implementations, or strategy state.
`tests/test_architecture.py` enforces this direction by inspecting source imports
without importing the package.

## Orchestration

`strategy`, `account`, `risk`, `stages`, and the factor DAG coordinate use
cases. They may depend on kernel contracts. Callback types and validation belong
in stateless collaborators such as `strategy_contracts` and
`callback_contracts`, not in the strategy state machine. Orchestrators should
receive external behavior as callables or protocols and should not add direct
filesystem, subprocess, or UI operations.

Trade-history reconciliation is isolated in `trade_reconciliation`; it consumes
trade value objects and must not depend on the mutable `Account` aggregate.

## Edges and composition

`factor_cli`, `pq_io`, `factor_store`, `return_reporting`, `interactive_plot`,
and notebook-facing helpers are adapters. They may depend inward on orchestration and contracts.
Keep serialization, widget rendering, logging, and process lifecycle at these
edges. Translate expected adapter failures with operation context; unexpected
programming errors should remain observable. A broad exception catch is allowed
only for documented cleanup followed by an immediate re-raise.

## Change rule

When extracting a use case, preserve the public API, add a characterization test,
and move one dependency edge at a time. Add a new abstraction only when it
separates a stable policy from a concrete effect or supports a second adapter.
