# Security Policy

Do not report suspected vulnerabilities in native file parsing through a public
issue. Contact the repository owner privately and include a minimal reproducer,
affected version, platform, and impact. Avoid attaching sensitive market data.

The project reads CSV, ZIP, YAML, and HDF5 inputs. Treat files from untrusted
sources as hostile until the native parser and persistence hardening work in
`ADVERSARIAL_REVIEW_PLAN.md` is complete.
