# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Cross-call embedding coalescing with a bounded successful-vector cache.

One wrapper instance coordinates every caller on the same event loop. Callers
that arrive inside the coalescing window, or while a request is still running,
share one underlying computation per distinct text. Cancelling one waiter never
cancels the shared work, and only successful vectors enter the LRU cache.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from powercontext.builtin.inference.models import EmbeddingResult, InferenceUsage

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.memory.models import EmbeddingProfile
    from powercontext.builtin.inference.protocols import EmbeddingModel

_Vector = tuple[float, ...]


@dataclass(frozen=True, slots=True)
class _BatchUsage:
    """Usage attributed to one underlying provider request."""

    usage: InferenceUsage


_Outcome = tuple[_Vector, _BatchUsage | None]


class CoalescingEmbeddingModel:
    """Share concurrent embedding work and cache successful vectors by text."""

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if isinstance(max_batch_size, bool) or not isinstance(max_batch_size, int) or max_batch_size < 1:
            raise ValueError("max_batch_size must be a positive integer")  # noqa: TRY003
        if isinstance(coalesce_ms, bool) or not isinstance(coalesce_ms, int | float) or coalesce_ms < 0:
            raise ValueError("coalesce_ms must be a non-negative number")  # noqa: TRY003
        if isinstance(cache_size, bool) or not isinstance(cache_size, int) or cache_size < 0:
            raise ValueError("cache_size must be a non-negative integer")  # noqa: TRY003
        self._model = model
        self._max_batch_size = max_batch_size
        self._coalesce_seconds = float(coalesce_ms) / 1_000
        self._cache_size = cache_size
        self._cache: OrderedDict[str, _Vector] = OrderedDict()
        self._pending: dict[str, asyncio.Future[_Outcome]] = {}
        self._inflight: dict[str, asyncio.Future[_Outcome]] = {}
        self._batch_task: asyncio.Task[None] | None = None

    @property
    def profile(self) -> EmbeddingProfile:
        """Expose the wrapped model's deployment-fixed embedding profile."""

        return self._model.profile

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Return one vector per input position, sharing pending provider work."""

        items = tuple(texts)
        if not items:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))
        unique = tuple(dict.fromkeys(items))
        resolved: dict[str, _Vector] = {}
        waiting: dict[str, asyncio.Future[_Outcome]] = {}
        for text in unique:
            cached = self._cache_lookup(text)
            if cached is not None:
                resolved[text] = cached
                continue
            future = self._inflight.get(text) or self._pending.get(text)
            if future is None:
                future = asyncio.get_running_loop().create_future()
                self._pending[text] = future
                self._ensure_batch_task()
            waiting[text] = future
        collected = await asyncio.gather(
            *(asyncio.shield(future) for future in waiting.values()),
            return_exceptions=True,
        )
        for result in collected:
            if isinstance(result, BaseException):
                raise result
        outcomes = dict(zip(waiting, collected, strict=True))
        vectors: list[_Vector] = []
        usages: dict[int, InferenceUsage] = {}
        for text in items:
            if text in resolved:
                vectors.append(resolved[text])
                continue
            vector, batch = outcomes[text]
            vectors.append(vector)
            if batch is not None:
                usages[id(batch)] = batch.usage
        return EmbeddingResult(vectors=tuple(vectors), usage=_merge_usage(tuple(usages.values())))

    def _ensure_batch_task(self) -> None:
        task = self._batch_task
        if task is not None and not task.done():
            return
        self._batch_task = asyncio.get_running_loop().create_task(self._run_batches())

    async def _run_batches(self) -> None:
        try:
            first = True
            while self._pending:
                if first:
                    first = False
                    await asyncio.sleep(self._coalesce_seconds)
                texts = tuple(self._pending)
                for start in range(0, len(texts), self._max_batch_size):
                    await self._run_batch(texts[start : start + self._max_batch_size])
        finally:
            self._batch_task = None

    async def _run_batch(self, batch: tuple[str, ...]) -> None:
        futures: dict[str, asyncio.Future[_Outcome]] = {}
        for text in batch:
            future = self._pending.pop(text, None)
            if future is None:
                continue
            self._inflight[text] = future
            futures[text] = future
        if not futures:
            return
        try:
            result = await self._model.embed(tuple(batch))
            vectors = tuple(tuple(vector) for vector in result.vectors)
            if len(vectors) != len(batch):
                raise ValueError("embedding provider returned the wrong vector count")  # noqa: TRY003, TRY301
            usage = _BatchUsage(usage=result.usage)
            for text, vector in zip(batch, vectors, strict=True):
                self._cache_store(text, vector)
                future = futures[text]
                if not future.done():
                    future.set_result((vector, usage))
        except asyncio.CancelledError:
            self._fail_futures(futures, asyncio.CancelledError())
            raise
        except Exception as error:
            self._fail_futures(futures, error)
        finally:
            for text in futures:
                self._inflight.pop(text, None)

    @staticmethod
    def _fail_futures(futures: dict[str, asyncio.Future[_Outcome]], error: BaseException) -> None:
        for future in futures.values():
            if not future.done():
                future.set_exception(error)

    def _cache_lookup(self, text: str) -> _Vector | None:
        if self._cache_size == 0:
            return None
        vector = self._cache.get(text)
        if vector is None:
            return None
        self._cache.move_to_end(text)
        return vector

    def _cache_store(self, text: str, vector: _Vector) -> None:
        if self._cache_size == 0:
            return
        self._cache[text] = vector
        self._cache.move_to_end(text)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)


def _merge_usage(usages: Iterable[InferenceUsage]) -> InferenceUsage:
    values = tuple(usages)
    if not values:
        return InferenceUsage(requests=0)
    return InferenceUsage(
        requests=sum(usage.requests for usage in values),
        input_tokens=_sum_defined(usage.input_tokens for usage in values),
        output_tokens=_sum_defined(usage.output_tokens for usage in values),
    )


def _sum_defined(values: Iterable[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return None if not present else sum(present)


__all__ = ["CoalescingEmbeddingModel"]
