Native CSV input limits
=======================

``gambit._io.read_file`` applies the same limits to plain CSV files and CSV
members read from ZIP archives:

* A single decompressed row may contain at most 16 MiB of data, including all
  selected and unselected fields. Larger rows raise ``RuntimeError``.
* Rows may end with LF or CRLF. The final row does not require a line ending.
* ``max_rows=0`` reads until EOF. Use a positive ``max_rows`` when processing
  untrusted or unexpectedly large inputs to bound output-array allocation.
* Input is parsed as bytes. Fixed-width ``S[n]`` columns preserve byte values;
  callers are responsible for decoding and validating text encodings.
* A ZIP member's declared decompressed size may not exceed 1 GiB. Larger
  members are rejected before decompression begins.

ZIP members are streamed and are not expanded into a temporary file. Both the
16 MiB row limit and 1 GiB member limit apply to decompressed sizes. Applications
accepting untrusted archives should additionally constrain the archive's total
size and number of members before calling the native reader.

Versioned HDF5 dataframes
=========================

``np_arrays_to_hdf5`` publishes dataframe groups using schema version 1. Each
committed group records a format identifier, schema version, state, UTC write
time, row count, JSON column manifest, and UTF-8 column manifest. Readers remain
compatible with legacy Gambit groups that used comma-separated manifests, but
reject unknown versioned schemas rather than guessing.

Replacement uses three sibling group names: the requested key, a pending group,
and a backup group. Gambit completely writes and flushes the pending group
before moving the previous committed group to backup and publishing the new
group. A later writer removes an incomplete pending group and restores a backup
when the requested key is absent. A read-only reader also falls back to the
committed backup after an interrupted swap. This protects group replacement;
it does not claim filesystem durability against storage-device failure.

Before mutation, writers require one-dimensional NumPy columns with equal row
counts and safe names. Readers validate the manifest, group state, dataset
types, dimensions, and declared row counts before allocation. The defaults
bound a group to 10,000 columns, 100 million rows, and 8 GiB of logical array
data. ``max_columns``, ``max_rows``, and ``max_bytes`` can be lowered for an
application's trust boundary. Variable-length external HDF5 datasets are
rejected because their allocation cannot be bounded from fixed schema metadata.
