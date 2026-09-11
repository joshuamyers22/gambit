from __future__ import annotations

import gc
import os
import random
import shlex
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

from gambit import _io

CORPUS = Path(__file__).parent / "corpus" / "native_io"
PROBE = Path(__file__).parent / "native_io_probe.py"


@pytest.mark.parametrize("dtype,bits", [("i4", 32), ("i8", 64), ("M8[ns]", 64)])
@pytest.mark.parametrize("zipped", [False, True])
def test_native_integer_limits_and_overflow(tmp_path, dtype, bits, zipped):
    path = tmp_path / "integers.csv"

    def read(payload):
        path.write_text(payload)
        source = str(path)
        if zipped:
            archive = tmp_path / "integers.zip"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
                output.write(path, "integers.csv")
            source = f"{archive}:integers.csv"
        return _io.read_file(source, [0], [dtype], skip_rows=0)[0]

    minimum, maximum = -(2 ** (bits - 1)), 2 ** (bits - 1) - 1
    values = read(f"{minimum}\n{maximum}\n0\n-0\n")
    assert values.view(f"i{bits // 8}").tolist() == [minimum, maximum, 0, 0]
    for invalid in (str(minimum - 1), str(maximum + 1), "9" * 1024, "-" + "9" * 1024):
        with pytest.raises(RuntimeError, match="integer value out of range"):
            read(f"1\n{invalid}\n")
        assert read("42\n").view(f"i{bits // 8}").tolist() == [42]


@pytest.mark.parametrize("dtype", ["i4", "i8"])
def test_integer_prefix_semantics_remain_compatible(tmp_path, dtype):
    path = tmp_path / "prefix.csv"
    path.write_text("1,234;ignored\n-1,234;ignored\n12tail;ignored\ninvalid;ignored\n")
    (values,) = _io.read_file(str(path), [0], [dtype], ";", 0, 0)
    assert values.tolist() == [1234, -1234, 12, 0]


@pytest.mark.parametrize("format", ["csv", "zip"])
def test_fuzz_target_seed_replay_under_sanitizers(tmp_path, format):
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment.pop("LD_PRELOAD", None)
    result = subprocess.run([
        sys.executable, str(root / "tools/run_native_fuzz.py"), "--format", format,
        "--output-dir", str(tmp_path / "replay"), "--replay-only",
    ], env=environment, capture_output=True, text=True, timeout=110)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"Passed {format} sanitizer seed replay (NOT coverage-guided)" in result.stdout


def test_numpy_data_allocation_failures_release_partial_results(tmp_path):
    probe = Path(__file__).with_name("native_numpy_allocator_probe.py")
    result = subprocess.run([
        sys.executable, str(probe), "--build-dir", str(tmp_path / "allocator"),
    ], capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "425 injected native failures; no retained data buffers or policies" in result.stdout


def test_native_reader_rejects_missing_file_without_crashing(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.csv"

    with pytest.raises(RuntimeError, match="can't read"):
        _io.read_file(str(missing), [0], ["i8"], ",", 0, 0)


def test_native_reader_rejects_empty_schema() -> None:
    with pytest.raises(RuntimeError, match="must not be empty"):
        _io.read_file(str(CORPUS / "invalid_numeric.csv"), [], [], ",", 0, 0)


def test_native_reader_has_safe_default_separator(tmp_path: Path) -> None:
    csv_file = tmp_path / "values.csv"
    csv_file.write_text("42\n")

    (values,) = _io.read_file(str(csv_file), [0], ["i8"], skip_rows=0)

    assert values.tolist() == [42]


def test_native_reader_cleans_up_invalid_string_width(tmp_path: Path) -> None:
    csv_file = tmp_path / "values.csv"
    csv_file.write_text("value\n")

    for _ in range(100):
        with pytest.raises(TypeError, match="item size"):
            _io.read_file(str(csv_file), [0], ["S0"], ",", 0, 0)


def test_native_reader_rejects_invalid_separator(tmp_path: Path) -> None:
    csv_file = tmp_path / "values.csv"
    csv_file.write_text("42\n")

    with pytest.raises(ValueError, match="exactly one byte"):
        _io.read_file(str(csv_file), [0], ["i8"], "", 0, 0)


def test_native_reader_max_rows_excludes_skipped_header(tmp_path: Path) -> None:
    csv_file = tmp_path / "values.csv"
    csv_file.write_text("value\n1\n2\n3\n")

    (values,) = _io.read_file(str(csv_file), [0], ["i8"], ",", 1, 2)

    assert values.tolist() == [1, 2]


def test_native_reader_rejects_missing_selected_field() -> None:
    with pytest.raises(RuntimeError, match="fields on row"):
        _io.read_file(
            str(CORPUS / "missing_column.csv"), [0, 1], ["S16", "f8"], ",", 1, 0
        )


def test_native_reader_allows_unselected_trailing_fields() -> None:
    symbols, prices = _io.read_file(
        str(CORPUS / "extra_column.csv"), [0, 1], ["S16", "f8"], ",", 1, 0
    )

    assert symbols.tolist() == [b"AAPL"]
    assert prices.tolist() == [100.0]


def test_native_reader_invalid_numeric_value_has_documented_nan_semantics() -> None:
    symbols, prices = _io.read_file(
        str(CORPUS / "invalid_numeric.csv"), [0, 1], ["S16", "f8"], ",", 1, 0
    )

    assert symbols.tolist() == [b"AAPL"]
    assert prices.shape == (1,)
    assert np.isnan(prices[0])


def test_native_reader_preserves_long_unterminated_final_row(tmp_path: Path) -> None:
    csv_file = tmp_path / "long-final-row.csv"
    symbol = "A" * (64 * 1024)
    csv_file.write_text(f"{symbol},100.5", encoding="ascii")

    symbols, prices = _io.read_file(str(csv_file), [0, 1], ["S65536", "f8"], ",", 0, 0)

    assert symbols.tolist() == [symbol.encode()]
    assert prices.tolist() == [100.5]


def test_native_reader_rejects_rows_over_input_limit(tmp_path: Path) -> None:
    csv_file = tmp_path / "oversized.csv"
    csv_file.write_bytes(b"A" * (16 * 1024 * 1024 + 1))

    with pytest.raises(RuntimeError, match="16 MiB input limit"):
        _io.read_file(str(csv_file), [0], ["S1"], ",", 0, 0)


def test_native_reader_preserves_non_utf8_bytes(tmp_path: Path) -> None:
    csv_file = tmp_path / "non-utf8.csv"
    csv_file.write_bytes(b"\xff,1.0\n")

    labels, values = _io.read_file(str(csv_file), [0, 1], ["S1", "f8"], ",", 0, 0)

    assert labels.tolist() == [b"\xff"]
    assert values.tolist() == [1.0]


def test_native_reader_reads_zip_member(tmp_path: Path) -> None:
    archive = tmp_path / "prices.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.writestr("prices.csv", "AAPL,100.5")

    symbols, prices = _io.read_file(
        f"{archive}:prices.csv", [0, 1], ["S16", "f8"], ",", 0, 0
    )

    assert symbols.tolist() == [b"AAPL"]
    assert prices.tolist() == [100.5]


def test_native_zip_reader_does_not_retain_archive_descriptors(tmp_path: Path) -> None:
    descriptor_directory = Path("/dev/fd")
    if not descriptor_directory.is_dir():
        pytest.skip("platform does not expose process file descriptors")
    baseline = len(list(descriptor_directory.iterdir()))

    for index in range(110):
        archive = tmp_path / f"prices-{index}.zip"
        with zipfile.ZipFile(archive, "w") as zip_file:
            zip_file.writestr("prices.csv", "AAPL,100.5")
        _io.read_file(f"{archive}:prices.csv", [0, 1], ["S16", "f8"], ",", 0, 0)

    gc.collect()
    assert len(list(descriptor_directory.iterdir())) <= baseline + 3


@pytest.mark.parametrize("truncate_bytes", [0, 12])
def test_native_reader_rejects_corrupt_or_truncated_zip(
    tmp_path: Path, truncate_bytes: int
) -> None:
    archive = tmp_path / f"broken-{truncate_bytes}.zip"
    if truncate_bytes == 0:
        archive.write_bytes(b"this is not a zip archive")
    else:
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
            zip_file.writestr("prices.csv", "AAPL,100.5\n")
        archive.write_bytes(archive.read_bytes()[:-truncate_bytes])

    with pytest.raises(RuntimeError, match="can't read"):
        _io.read_file(f"{archive}:prices.csv", [0, 1], ["S16", "f8"], ",", 0, 0)


def test_native_reader_rejects_missing_zip_member(tmp_path: Path) -> None:
    archive = tmp_path / "missing-member.zip"
    with zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr("other.csv", "AAPL,100.5\n")

    with pytest.raises(RuntimeError, match="can't inspect"):
        _io.read_file(f"{archive}:prices.csv", [0, 1], ["S16", "f8"], ",", 0, 0)


def test_seeded_malformed_inputs_do_not_crash_native_reader(tmp_path: Path) -> None:
    rng = random.Random(20260827)
    alphabet = b"ABCDEF0123456789,\r\n\x00\xff"
    case_files = []

    for case_number in range(24):
        payload = bytes(rng.choice(alphabet) for _ in range(rng.randrange(0, 4096)))
        case_file = tmp_path / f"fuzz-{case_number:02d}.csv"
        case_file.write_bytes(payload)
        case_files.append(case_file)

    result = subprocess.run(
        [sys.executable, str(PROBE), *(str(path) for path in case_files)],
        check=False,
        capture_output=os.environ.get("GAMBIT_SANITIZER_RUN") != "1",
        timeout=10,
    )
    assert result.returncode == 0, (
        "native reader terminated for fuzz seed 20260827: "
        f"stderr={(result.stderr or b'').decode(errors='replace')}"
    )


@pytest.mark.parametrize(
    ("indices", "dtypes", "message"),
    [
        ([1, 0], ["i8", "i8"], "monotonically increasing"),
        ([0], ["i8", "i8"], "same size"),
        ([0], ["object"], "expected i1, i4, i8, f4, f8"),
        ([0], [""], "expected i1, i4, i8, f4, f8"),
    ],
)
def test_native_reader_rejects_invalid_schema(indices, dtypes, message: str) -> None:
    with pytest.raises((RuntimeError, TypeError), match=message):
        _io.read_file(str(CORPUS / "invalid_numeric.csv"), indices, dtypes, ",", 1, 0)


@pytest.mark.parametrize("zipped", [False, True])
@pytest.mark.parametrize("budget,accepted", [(20, True), (19, False)])
def test_total_output_budget_across_columns_and_rows(tmp_path, zipped, budget, accepted):
    path = tmp_path / "budget.csv"
    path.write_text("abcdef,1\nghijkl,2\n")
    source = str(path)
    if zipped:
        archive = tmp_path / "budget.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            output.write(path, "budget.csv")
        source = f"{archive}:budget.csv"
    if not accepted:
        with pytest.raises(RuntimeError, match="output byte limit"):
            _io.read_file(source, [0, 1], ["S2", "i8"], skip_rows=0, max_output_bytes=budget)
    else:
        labels, numbers = _io.read_file(source, [0, 1], ["S2", "i8"], skip_rows=0, max_output_bytes=budget)
        assert labels.tolist() == [b"ab", b"gh"] and numbers.tolist() == [1, 2]
        assert labels.flags.owndata and numbers.flags.owndata
        assert labels.nbytes + numbers.nbytes == budget


@pytest.mark.parametrize("zipped", [False, True])
@pytest.mark.parametrize("ending", [b"\n", b"\r\n", b""])
def test_input_budget_includes_headers_and_unselected_bytes(tmp_path, zipped, ending):
    payload = b"header,ignored\n42,unselected" + ending
    path = tmp_path / "input.csv"
    path.write_bytes(payload)
    source = str(path)
    if zipped:
        archive = tmp_path / "input.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            output.writestr("input.csv", payload)
        source = f"{archive}:input.csv"
    (values,) = _io.read_file(source, [0], ["i8"], max_input_bytes=len(payload))
    assert values.tolist() == [42]
    with pytest.raises(RuntimeError, match="input byte limit"):
        _io.read_file(source, [0], ["i8"], max_input_bytes=len(payload) - 1)


@pytest.mark.parametrize("name", ["max_input_bytes", "max_output_bytes", "max_columns"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, "1", None, 2**100])
def test_native_limits_reject_invalid_values(tmp_path, name, value):
    path = tmp_path / "limits.csv"
    path.write_text("1\n")
    with pytest.raises((TypeError, ValueError, OverflowError)):
        _io.read_file(str(path), [0], ["i8"], skip_rows=0, **{name: value})


def test_column_limit_is_checked_before_file_open(tmp_path):
    with pytest.raises(RuntimeError, match="column limit"):
        _io.read_file(str(tmp_path / "missing"), [0, 1], ["i8", "i8"], max_columns=1)


def test_huge_string_width_fails_before_allocating_or_reading(tmp_path):
    with pytest.raises(RuntimeError, match="output byte limit"):
        _io.read_file(str(tmp_path / "missing"), [0], ["S1000000000"], max_output_bytes=16)


@pytest.mark.parametrize("dtype", ["S-1", "S0", "S", "S999999999999999999999", "S8trailing", "S8\x00ignored"])
def test_string_width_uses_checked_complete_decimal_parse(tmp_path, dtype):
    with pytest.raises(TypeError, match="item size|NUL"):
        _io.read_file(str(tmp_path / "missing"), [0], [dtype], max_output_bytes=16)


def test_max_rows_does_not_eagerly_reserve_requested_capacity(tmp_path):
    path = tmp_path / "one.csv"
    path.write_text("42\n")
    (values,) = _io.read_file(str(path), [0], ["i8"], skip_rows=0, max_rows=2**31 - 1, max_output_bytes=8)
    assert values.tolist() == [42]


def test_max_rows_stops_before_parsing_an_unneeded_oversized_row(tmp_path):
    path = tmp_path / "unneeded.csv"
    path.write_bytes(b"42\n" + b"A" * (16 * 1024 * 1024 + 1))
    (values,) = _io.read_file(str(path), [0], ["i8"], skip_rows=0, max_rows=1, max_output_bytes=8)
    assert values.tolist() == [42]


def test_resource_failures_release_zip_descriptors_and_allow_retry(tmp_path):
    archive = tmp_path / "retry.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr("data.csv", "abcd,1\nefgh,2\n")
    descriptors = Path("/dev/fd")
    before = len(list(descriptors.iterdir())) if descriptors.is_dir() else None
    for _ in range(100):
        with pytest.raises(RuntimeError, match="output byte limit"):
            _io.read_file(f"{archive}:data.csv", [0, 1], ["S4", "i8"], skip_rows=0, max_output_bytes=12)
        with pytest.raises(RuntimeError, match="can't inspect"):
            _io.read_file(f"{archive}:missing.csv", [0], ["i8"], skip_rows=0)
    labels, values = _io.read_file(f"{archive}:data.csv", [0, 1], ["S4", "i8"], skip_rows=0, max_output_bytes=24)
    assert labels.tolist() == [b"abcd", b"efgh"] and values.tolist() == [1, 2]
    gc.collect()
    if before is not None:
        assert len(list(descriptors.iterdir())) <= before + 3


def test_native_csv_allocation_failures_unwind_all_cpp_owners(tmp_path):
    root = Path(__file__).resolve().parents[1]
    source = tmp_path / "allocations.csv"
    source.write_text("abcdef,1\nghijkl,2\nmnopqr,3\n")
    archive = tmp_path / "allocations.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.write(source, "allocations.csv")
    if shutil.which("pkg-config"):
        zip_flags = shlex.split(subprocess.check_output(["pkg-config", "--cflags", "--libs", "libzip"], text=True))
    else:
        zip_flags = ["-lzip"]
        for prefix in (os.environ.get("LIBZIP_PREFIX"), "/opt/homebrew/opt/libzip", "/usr/local/opt/libzip"):
            if prefix and (Path(prefix) / "include").is_dir():
                zip_flags.extend(["-I", str(Path(prefix) / "include"), "-L", str(Path(prefix) / "lib")])
    executable = tmp_path / "allocation-probe"
    sanitizers = ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
    subprocess.run([
        *shlex.split(os.environ.get("CXX", "c++")), "-std=c++11", "-O1", "-g", *sanitizers,
        "-I", str(root / "src/gambit/cpp/io"),
        str(root / "tests/native_csv_allocation_probe.cpp"), str(root / "src/gambit/cpp/io/csv_reader.cpp"),
        *zip_flags, "-o", str(executable),
    ], check=True, capture_output=True, timeout=60)
    environment = dict(os.environ, ASAN_OPTIONS=f"detect_leaks={int(sys.platform != 'darwin')}:halt_on_error=1",
                       UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1")
    for filename in [str(source), f"{archive}:allocations.csv"]:
        result = subprocess.run([str(executable), filename], env=environment,
                                capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "allocation failures" in result.stdout
