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

ZIP members are streamed and are not expanded into a temporary file. The
16 MiB row limit applies after decompression, but there is currently no total
decompressed-file-size limit. Applications accepting untrusted archives should
also enforce archive and member size limits before calling the native reader.
