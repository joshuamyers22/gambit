import sys

import numpy as np
import pytest

from gambit.sparse_iterations import SparseIterations


def test_logical_billion_bucket_grid_does_not_allocate_per_timestamp():
    small = SparseIterations[int](10)
    large = SparseIterations[int](1_000_000_000)
    assert len(large) == 1_000_000_000
    assert sys.getsizeof(small._buckets) == sys.getsizeof(large._buckets)
    assert large.populated_count == 0
    for index in range(1000):
        assert large[index] == []
        assert large.at(index) == ()
    assert large.populated_count == 0
    large[-1].append(7)
    assert large.populated_count == 1
    assert list(large.at(999_999_999)) == [7]
    large[-1].clear()
    assert large.populated_count == 0


def test_bucket_views_are_live_without_materializing_empty_reads():
    buckets = SparseIterations[int](4)
    view = buckets[2]
    assert view == [] and not view
    buckets.append_at(2, 3)
    assert view == [3]
    view.extend([4, 5])
    assert buckets[2] == [3, 4, 5]
    assert buckets[1] == []
    assert buckets.populated_count == 1
    view[1:] = [8, 9]
    assert view.pop() == 9
    view.insert(0, 6)
    assert view == [6, 3, 8]
    view.reverse()
    assert view == [8, 3, 6]
    assert list(buckets[::2]) == [[], [8, 3, 6]]
    view[:] = []
    assert buckets.populated_count == 0


def test_bucket_index_and_slice_behavior_matches_lists():
    buckets = SparseIterations[int](3)
    buckets[np.int64(1)].append(9)
    assert buckets[-2] == [9]
    assert buckets[::-1] == [[], [9], []]
    assert buckets[1][:] == [9]
    assert repr(buckets[1]) == "[9]"
    for index in (-4, 3):
        with pytest.raises(IndexError):
            buckets[index]
        with pytest.raises(IndexError):
            buckets.at(index)
        with pytest.raises(IndexError):
            buckets.append_at(index, 2)
    assert buckets.populated_count == 1


def test_failed_bucket_mutation_leaves_storage_unchanged():
    buckets = SparseIterations[int](2)
    with pytest.raises(IndexError):
        buckets[0][0] = 4
    with pytest.raises(IndexError):
        del buckets[0][0]
    assert buckets.populated_count == 0
    buckets[1].extend([1, 2, 3])
    with pytest.raises(ValueError):
        buckets[1][::2] = [8]
    assert buckets[1] == [1, 2, 3]


@pytest.mark.parametrize("size", [-1, sys.maxsize + 1])
def test_invalid_logical_size_is_rejected(size):
    with pytest.raises(ValueError):
        SparseIterations(size)


def test_no_implicit_dense_storage_during_iteration():
    buckets = SparseIterations[int](100)
    assert all(bucket == [] for bucket in buckets)
    assert buckets.populated_count == 0
    assert len(SparseIterations(0)) == 0
    with pytest.raises(TypeError):
        SparseIterations(1.5)


def test_explicit_slice_materializes_only_views_and_preserves_write_through():
    buckets = SparseIterations[int](5)
    sliced = buckets[1:4]
    assert buckets.populated_count == 0
    sliced[0].append(10)
    assert buckets[1] == [10]
    assert buckets.populated_count == 1
