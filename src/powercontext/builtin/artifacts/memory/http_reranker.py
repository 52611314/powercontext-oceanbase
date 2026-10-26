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

"""HTTP cross-encoder reranking for coarse Memory search candidates."""

from __future__ import annotations

import json
import math
from typing import Any

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage

HTTP_RERANK_POLICY_ID = "http-cross-encoder-v1"


class HTTPMemoryReranker:
    """Score a bounded candidate pool through a batched HTTP reranking service.

    The service owns the relevance scores; this adapter owns the protocol. It keeps the
    candidate's position in the complete input as its one-based ``rank``, sends every rank
    exactly once, and applies one global stable selection across all batches.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = HTTP_RERANK_POLICY_ID,
    ) -> None:
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("batch_size must be a positive integer")  # noqa: TRY003
        if not isinstance(policy_id, str) or not policy_id:
            raise ValueError("policy_id must be a non-empty string")  # noqa: TRY003
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Score every candidate once, then select the globally highest scores.

        Batches are sent in ascending rank order. Each response may return its ranks in any
        order, but it must cover exactly the ranks that batch carried. Scores are accumulated
        before selecting, so a later batch can outrank an earlier one.
        """

        _validate_bounds(len(candidates), limit)
        scores: dict[int, float] = {}
        requests = 0
        for start in range(0, len(candidates), self._batch_size):
            batch = candidates[start : start + self._batch_size]
            payload = {
                "query": query,
                "candidates": [
                    {"rank": start + offset, "text": candidate.text} for offset, candidate in enumerate(batch, start=1)
                ],
            }
            response = await self._client.post(self._endpoint, json=payload)
            _require_success(response)
            requests += 1
            scores.update(_parse_scores(response, first_rank=start + 1, count=len(batch)))

        ordered = sorted(scores, key=lambda rank: (-scores[rank], rank))[:limit]
        return MemoryRerankDecision(
            selected_ranks=tuple(ordered),
            usage=InferenceUsage(requests=requests),
        )


def _validate_bounds(candidate_count: int, limit: int) -> None:
    if candidate_count < 1:
        raise ValueError("rerank requires at least one candidate")  # noqa: TRY003
    if limit < 1 or limit > candidate_count:
        raise ValueError("rerank limit must be between one and the candidate count")  # noqa: TRY003


def _require_success(response: httpx.Response) -> None:
    """Keep HTTP failures as httpx errors, including non-2xx responses raise_for_status skips."""

    response.raise_for_status()
    if not response.is_success:
        raise httpx.HTTPStatusError(  # noqa: TRY003
            f"reranker returned HTTP {response.status_code}",
            request=response.request,
            response=response,
        )


def _parse_scores(response: httpx.Response, *, first_rank: int, count: int) -> dict[int, float]:
    """Validate one batch response and return its rank-to-score mapping."""

    payload = _decode_payload(response)
    if set(payload) != {"scores"}:
        raise ValueError("reranker response must contain exactly the scores field")  # noqa: TRY003
    rows = payload["scores"]
    if not isinstance(rows, list):
        raise ValueError("reranker scores must be a JSON array")  # noqa: TRY003, TRY004

    expected = frozenset(range(first_rank, first_rank + count))
    scores: dict[int, float] = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"rank", "score"}:
            raise ValueError("each reranker score must contain exactly rank and score")  # noqa: TRY003
        rank = row["rank"]
        if type(rank) is not int or rank not in expected:
            raise ValueError("reranker score rank must belong to this batch")  # noqa: TRY003
        if rank in scores:
            raise ValueError("reranker score rank must be reported at most once")  # noqa: TRY003
        scores[rank] = _finite_score(row["score"])
    if scores.keys() != expected:
        raise ValueError("reranker scores must cover every rank in this batch")  # noqa: TRY003
    return scores


def _decode_payload(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = json.loads(response.content, parse_constant=_reject_json_constant)
    except ValueError as error:
        raise ValueError("reranker response is not valid JSON") from error  # noqa: TRY003
    if not isinstance(payload, dict):
        raise ValueError("reranker response must be a JSON object")  # noqa: TRY003, TRY004
    return payload


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"reranker response contains non-finite JSON constant {value}")  # noqa: TRY003


def _finite_score(value: Any) -> float:
    if type(value) not in (int, float):
        raise ValueError("reranker score must be a JSON number")  # noqa: TRY003
    try:
        score = float(value)
    except (OverflowError, ValueError) as error:
        raise ValueError("reranker score must be a finite number") from error  # noqa: TRY003
    if not math.isfinite(score):
        raise ValueError("reranker score must be finite")  # noqa: TRY003
    return score


__all__ = ["HTTP_RERANK_POLICY_ID", "HTTPMemoryReranker"]
