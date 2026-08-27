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
