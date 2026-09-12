# Security Policy

Do not report suspected vulnerabilities in native file parsing through a public
issue. Contact the repository owner privately and include a minimal reproducer,
affected version, platform, and impact. Avoid attaching sensitive market data.

The project reads CSV, ZIP, YAML, and HDF5 inputs. Treat files from untrusted
sources as hostile until the native parser and persistence hardening work in
`PRODUCTION_READINESS_PLAN.md` is complete. Result bundles and experimental mapped
caches also cross file/dependency trust boundaries. The [draft threat model](THREAT_MODEL.md)
records controls, caller responsibilities and residual risks; it is not security approval.
