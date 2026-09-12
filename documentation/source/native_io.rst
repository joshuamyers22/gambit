Native CSV input limits
=======================

``gambit._io.read_file`` applies the same limits to plain CSV files and CSV
members read from ZIP archives:

* A single decompressed row may contain at most 16 MiB of data, including all
  selected and unselected fields. Larger rows raise ``RuntimeError``.
* Rows may end with LF or CRLF. The final row does not require a line ending.
* ``max_rows=0`` reads until EOF, subject to byte budgets. A positive ``max_rows``
  caps returned rows after skipped headers and stops before parsing another row.
  It no longer causes an eager allocation for that many rows.
* Input is parsed as bytes. Fixed-width ``S[n]`` columns preserve byte values;
  callers are responsible for decoding and validating text encodings.
* ``i4``, ``i8`` and integer-backed datetime columns accept the full signed range.
  Out-of-range numeric prefixes raise ``RuntimeError`` instead of overflowing;
  no partial result is returned. Existing prefix/separator parsing is retained,
  not replaced with strict whole-token validation. Legacy ``i1`` narrowing is
  unchanged. Validate inputs and rerun results affected by overflowed integers.
* A ZIP member's declared decompressed size may not exceed 1 GiB. Larger
  members are rejected before decompression begins.
* ``max_input_bytes`` defaults to 1 GiB. Actual bytes read/decompressed, including
  skipped headers, unselected fields, line endings, and read-ahead, are counted.
  The reader checks at most one byte beyond the budget to distinguish EOF from
  excess input. A ZIP member whose declared size exceeds this budget is rejected
  before reading, even when ``max_rows`` requests only a prefix. The independent
  1 GiB ZIP-member ceiling cannot be raised with this option.
* ``max_output_bytes`` defaults to 256 MiB across **all** returned NumPy columns.
  The sum of dtype widths times accepted rows is checked before appending a row;
  exceeding it raises ``RuntimeError`` without publishing partial columns.
  Fixed-width strings are truncated/padded while parsing, not retained at their
  original token lengths. A schema wider than the output budget fails up front.
* ``max_columns`` defaults to 4,096 selected columns. Dtype strings are limited to
  64 characters, and string widths must be fully specified positive decimal
  integers fitting a C ``int``. Limits must be positive integers, not booleans,
  fitting ``Py_ssize_t``. Invalid configuration fails before file access.

For a smaller workload budget::

   from gambit import _io

   labels, prices = _io.read_file(
       "prices.csv", [0, 1], ["S16", "f8"], skip_rows=1,
       max_input_bytes=8 * 1024 * 1024,
       max_output_bytes=2 * 1024 * 1024,
       max_columns=2,
   )

Columns use automatically owned packed buffers. NumPy allocates and owns each
returned array; references and buffers unwind on conversion/read failure, and
the GIL is restored before translating C++ exceptions. Native allocation failure
becomes ``MemoryError``. The returned-byte budget is not a total RSS cap: staging
capacity/growth, array copies, the bounded row buffer, schema objects, and libzip
also consume memory. Use process-level memory/time isolation for hard ceilings.

ZIP members are streamed and are not expanded into a temporary file. Both the
16 MiB row limit and 1 GiB member limit apply to decompressed sizes. Applications
accepting untrusted archives should additionally constrain the archive's total
size and number of members before calling the native reader. The budget covers
the selected member, not libzip's archive-directory parsing or aggregate work
across multiple calls. It is not a timeout or a general hostile-file sandbox.

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
counts and safe names. Readers preflight **every** selected dataset and the
aggregate budget before reading any column payload. Manifest row counts and
schema versions must be integer scalars, not booleans, strings or floats.
Column names must be simple names, and keys/column names cannot contain NUL.

The defaults bound a group to 10,000 columns, 100 million rows, and 8 GiB of
estimated returned array data. ``max_columns``, ``max_rows``, and ``max_bytes``
can be lowered. Fixed-width bytes are returned as NumPy Unicode; the byte budget
now conservatively charges four bytes per source byte, including UTF-8 columns.
This can reject files admitted by the former source-byte-only estimate. Each
selected output counts separately, even if names hard-link the same dataset.

The array reader also accepts ``max_manifest_bytes`` (default 1 MiB), bounding
the combined UTF-8 size of the column and UTF-8-column manifests before JSON
parsing or legacy comma splitting. This is a reader admission limit, not a
writer limit; unusually large trusted manifests require an explicit override.
Manifests and type/format/state markers must be text scalars (UTF-8 byte scalars
are also accepted). Missing required attributes, invalid text/JSON and excessive
JSON nesting raise ``ValueError``. Both manifest lists are limited by
``max_columns`` before name validation. Optional UTF-8 manifests retain their
empty defaults.

Readers resolve group paths, backup groups and columns through hard links only.
Soft links, external links, virtual datasets and external raw-data storage are
rejected before resolving links or reading payloads. Variable-length types,
including those nested in compound dtypes, are rejected. UTF-8 manifest entries
must refer to fixed-width byte datasets. Materialize formerly linked/virtual
inputs into ordinary datasets in a trusted preparation step before loading them.
Normal versioned and legacy files, nested hard-linked groups, same-file hard-link
aliases and interrupted-swap backup reads remain supported.

This is not a complete HDF5 sandbox: file opening and native metadata loading
happen before these checks. In particular, h5py materializes attributes before
the manifest byte limit is enforced. Filters/decompression consume additional resources, and
temporary decoding buffers are not a process-RSS guarantee. The caller must
trust the filesystem and writer operations, restrict filters/plugins, and use
process-level memory/time/filesystem isolation for hostile files.
