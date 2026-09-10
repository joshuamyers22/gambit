"""Fixed-length timestamp buckets with storage only for populated indices.

This is an internal scheduling/debugging container, not a bounded audit sink.
Indexing returns a live list-like bucket; empty reads do not retain allocations.
The execution loop uses ``at`` to avoid even creating an empty bucket view.
"""

from __future__ import annotations

import operator
import sys
from collections.abc import Iterable, Iterator, MutableSequence, Sequence
from typing import Generic, TypeVar, overload

T = TypeVar("T")


class SparseIterations(Sequence[MutableSequence[T]], Generic[T]):
    def __init__(self, size: int) -> None:
        size = operator.index(size)
        if size < 0 or size > sys.maxsize:
            raise ValueError("timestamp bucket count is outside the supported range")
        self._size = size
        self._buckets: dict[int, list[T]] = {}

    def __len__(self) -> int:
        return self._size

    @property
    def populated_count(self) -> int:
        return len(self._buckets)

    def _index(self, index: int) -> int:
        index = operator.index(index)
        if index < 0:
            index += self._size
        if not 0 <= index < self._size:
            raise IndexError("timestamp bucket index out of range")
        return index

    @overload
    def __getitem__(self, index: int) -> MutableSequence[T]: ...

    @overload
    def __getitem__(self, index: slice) -> list[MutableSequence[T]]: ...

    def __getitem__(self, index: int | slice) -> MutableSequence[T] | list[MutableSequence[T]]:
        if isinstance(index, slice):
            return [self[position] for position in range(*index.indices(self._size))]
        return _Bucket(self, self._index(index))

    def at(self, index: int) -> Sequence[T]:
        """Internal read path; do not mutate the returned sequence."""
        return self._buckets.get(self._index(index), ())

    def append_at(self, index: int, value: T) -> None:
        index = self._index(index)
        bucket = self._buckets.get(index)
        if bucket is None:
            bucket = []
            self._buckets[index] = bucket
        bucket.append(value)

    def _replace(self, index: int, values: list[T]) -> None:
        if values:
            self._buckets[index] = values
        else:
            self._buckets.pop(index, None)


class _Bucket(MutableSequence[T]):
    def __init__(self, owner: SparseIterations[T], index: int) -> None:
        self._owner = owner
        self._index = index

    def __len__(self) -> int:
        return len(self._owner.at(self._index))

    def __iter__(self) -> Iterator[T]:
        return iter(self._owner.at(self._index))

    @overload
    def __getitem__(self, index: int) -> T: ...

    @overload
    def __getitem__(self, index: slice) -> list[T]: ...

    def __getitem__(self, index: int | slice) -> T | list[T]:
        values = self._owner.at(self._index)
        return list(values[index]) if isinstance(index, slice) else values[index]

    @overload
    def __setitem__(self, index: int, value: T) -> None: ...

    @overload
    def __setitem__(self, index: slice, value: Iterable[T]) -> None: ...

    def __setitem__(self, index, value) -> None:
        values = list(self._owner.at(self._index))
        values[index] = value
        self._owner._replace(self._index, values)

    def __delitem__(self, index: int | slice) -> None:
        values = list(self._owner.at(self._index))
        del values[index]
        self._owner._replace(self._index, values)

    def insert(self, index: int, value: T) -> None:
        values = list(self._owner.at(self._index))
        values.insert(index, value)
        self._owner._replace(self._index, values)

    def append(self, value: T) -> None:
        self._owner.append_at(self._index, value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, (list, _Bucket)):
            return list(self) == list(other)
        return NotImplemented

    def __repr__(self) -> str:
        return repr(list(self))
