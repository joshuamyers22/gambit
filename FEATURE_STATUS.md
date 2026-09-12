# Gambit feature status

This is the canonical release-posture matrix for Gambit 1.1.0. API stability
describes compatibility; it does not imply production qualification. Until all
P0 gates in [PRODUCTION_READINESS_PLAN.md](PRODUCTION_READINESS_PLAN.md) are
accepted, the distribution remains **Beta** and must not be described as
production/stable.

| Capability | Current posture | Production-stable gate |
|---|---|---|
| General `Strategy`, accounting, execution, risk, and result bundles | Release candidate; public API compatibility applies, but production qualification is pending | P0 correctness, reproducibility, governance, and release-drill evidence |
| Native CSV/ZIP and HDF5 ingestion | Release candidate only for documented formats, limits, and caller-owned inputs | Native boundary qualification for the declared input trust model |
| Option pricing, implied volatility, expiry, and settlement | Experimental | P1.4 lifecycle definition plus independent numerical qualification |
| Native factor cache, tick ring, and top-of-book/FIFO replay | Experimental | Separate correctness, recovery, capacity, and performance acceptance contract |
| Factor-cache CLI | In-environment maintenance utility; not an independently operated service | New deployment decision and operational acceptance if its role changes |
| Live trading, brokerage connectivity, and production order routing | Out of scope | Separate product boundary and approval |

“Release candidate” means the capability is being hardened against the stated
gate. It is not a production-readiness claim. Experimental APIs may change and
must not be included in stable product claims merely because they are importable.

