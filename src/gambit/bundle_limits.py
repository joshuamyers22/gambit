"""Resource policy for loading result bundles from local files."""

from dataclasses import dataclass, fields


class BacktestBundleError(ValueError):
    """A result bundle is malformed, unsupported, corrupt, or over budget."""


@dataclass(frozen=True)
class BundleLoadLimits:
    """Finite ceilings; decoded bytes are a conservative payload estimate, not RSS.

    Includes per-cell conversion headroom and referenced buffer bytes. Python,
    allocator, and native-library overhead still require OS-level isolation when
    a hard process-memory or execution-time ceiling is necessary.
    """

    max_manifest_bytes: int = 1024 * 1024
    max_table_bytes: int = 64 * 1024 * 1024
    max_total_bytes: int = 256 * 1024 * 1024
    max_table_rows: int = 1_000_000
    max_total_rows: int = 4_000_000
    max_columns: int = 128
    max_record_batches: int = 1024
    max_ipc_metadata_bytes: int = 1024 * 1024
    max_table_decoded_bytes: int = 256 * 1024 * 1024
    max_total_decoded_bytes: int = 512 * 1024 * 1024

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{item.name} must be a positive integer")
