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

"""seekdb Memory search using one native statement for hybrid candidates.

The FTS candidates come from the FULLTEXT index on the active-head projection and
the vector candidates from the HNSW index on the rebuildable embedding table. In
hybrid mode both branches share one statement, so scope, Memory, tag, and
candidate-limit filtering all execute inside seekdb.
"""

from __future__ import annotations

from pyobvector import VECTOR
from sqlalchemy import Table, bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import (
    CapabilityNotSupportedError,
    EmbeddingProfile,
    MemoryCapabilities,
    MemoryChannelHit,
    MemoryProjection,
    MemorySearchChannels,
    MemorySearchRequest,
)
from powercontext.builtin.artifacts.memory.canonical import validate_embedding
from powercontext.builtin.persistence.oceanbase.memory_index import (
    OceanBaseMemoryFTSIndex,
    OceanBaseMemoryVectorIndex,
)
from powercontext.builtin.persistence.tags import memory_tag_parameters, memory_tag_sql

_HYBRID_TEMPLATE = """
(SELECT 'fts' AS channel,
        h.memory_artifact_id, h.head_revision, h.entry_id, h.entry_version_id,
        v.text, NULL AS distance
 FROM pc_memory_entry_heads AS h
 JOIN pc_memory_entry_versions AS v
   ON v.scope_id = h.scope_id
  AND v.memory_artifact_id = h.memory_artifact_id
  AND v.entry_version_id = h.entry_version_id
 WHERE h.scope_id = :scope_id
   AND h.memory_artifact_id IN :fts_artifact_ids
   AND MATCH (h.searchable_text) AGAINST (:query)
   {fts_tags}
 ORDER BY MATCH (h.searchable_text) AGAINST (:query) DESC,
          h.memory_artifact_id, h.entry_id, h.entry_version_id
 LIMIT :candidate_limit)
UNION ALL
(SELECT 'vector' AS channel,
        m.memory_artifact_id, m.head_revision, m.entry_id, m.entry_version_id,
        v.text, l2_distance(m.embedding, :query_vector) AS distance
 FROM pc_memory_vector_entries AS m
 JOIN pc_memory_entry_versions AS v
   ON v.scope_id = m.scope_id
  AND v.memory_artifact_id = m.memory_artifact_id
  AND v.entry_version_id = m.entry_version_id
 WHERE m.scope_id = :scope_id
   AND m.memory_artifact_id IN :vector_artifact_ids
   {vector_tags}
 ORDER BY l2_distance(m.embedding, :query_vector) APPROXIMATE
 LIMIT :candidate_limit)
"""


class SeekDBMemoryIndex:
    """Expose seekdb FTS, vector, and single-statement hybrid Memory search."""

    def __init__(self, profile: EmbeddingProfile) -> None:
        self.profile = profile
        self._fts = OceanBaseMemoryFTSIndex()
        self._vector = OceanBaseMemoryVectorIndex(profile)
        self.capabilities = MemoryCapabilities(
            fts=True,
            vector=True,
            hybrid=True,
            tag_filter=True,
            embedding_profile=profile,
        )
        self.tables: tuple[Table, ...] = self._vector.tables
        self._hybrid = text(_HYBRID_TEMPLATE.format(fts_tags="", vector_tags="")).bindparams(
            bindparam("fts_artifact_ids", expanding=True),
            bindparam("vector_artifact_ids", expanding=True),
            bindparam("query_vector", type_=VECTOR(profile.dimension)),
        )
        self._hybrid_tagged = text(
            _HYBRID_TEMPLATE.format(fts_tags=memory_tag_sql("h"), vector_tags=memory_tag_sql("m"))
        ).bindparams(
            bindparam("fts_artifact_ids", expanding=True),
            bindparam("vector_artifact_ids", expanding=True),
            bindparam("tag_keys", expanding=True),
            bindparam("tag_hashes", expanding=True),
            bindparam("query_vector", type_=VECTOR(profile.dimension)),
        )

    async def initialize(self, connection: AsyncConnection, /) -> None:
        await self._fts.initialize(connection)
        await self._vector.initialize(connection)

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        memory_ref: ArtifactRef,
        projections: tuple[MemoryProjection, ...],
        /,
    ) -> None:
        await self._vector.replace(connection, scope_id, memory_ref, projections)

    async def delete(
        self,
        connection: AsyncConnection,
        scope_id: str,
        memory_ref: ArtifactRef,
        entry_ids: tuple[str, ...],
        /,
    ) -> None:
        await self._vector.delete(connection, scope_id, memory_ref, entry_ids)

    async def upsert(
        self,
        connection: AsyncConnection,
        scope_id: str,
        memory_ref: ArtifactRef,
        projections: tuple[MemoryProjection, ...],
        /,
    ) -> None:
        await self._vector.upsert(connection, scope_id, memory_ref, projections)

    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: MemorySearchRequest,
        /,
    ) -> MemorySearchChannels:
        if request.mode == "fts":
            return await self._fts.search(connection, scope_id, request)
        if request.mode == "vector":
            return await self._vector.search(connection, scope_id, request)
        if request.mode != "hybrid":
            return MemorySearchChannels()
        return await self._search_hybrid(connection, scope_id, request)

    async def _search_hybrid(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: MemorySearchRequest,
        /,
    ) -> MemorySearchChannels:
        if request.embedding_profile != self.profile or request.query_vector is None:
            raise CapabilityNotSupportedError("embedding-profile")
        if not await self._vector.vector_complete(connection, scope_id, request.memories, self.profile):
            raise CapabilityNotSupportedError("vector")
        artifact_ids = tuple(memory.artifact_id for memory in request.memories)
        parameters: dict[str, object] = {
            "scope_id": scope_id,
            "fts_artifact_ids": artifact_ids,
            "vector_artifact_ids": artifact_ids,
            "query": request.analyzed_query,
            "query_vector": validate_embedding(request.query_vector, dimension=self.profile.dimension),
            "candidate_limit": request.candidate_limit,
        }
        if request.tag_filter is None:
            statement = self._hybrid
        else:
            statement = self._hybrid_tagged
            parameters.update(memory_tag_parameters(request.tag_filter))
        rows = (await connection.execute(statement, parameters)).mappings()
        requested = {memory.artifact_id: memory for memory in request.memories}
        fts: list[MemoryChannelHit] = []
        vector: list[MemoryChannelHit] = []
        for row in rows:
            memory = requested.get(str(row["memory_artifact_id"]))
            if memory is None:
                continue
            hit = MemoryChannelHit(
                memory_ref=memory,
                entry_id=str(row["entry_id"]),
                entry_version_id=str(row["entry_version_id"]),
                text=str(row["text"]),
                distance=None if row["distance"] is None else float(row["distance"]),
            )
            if str(row["channel"]) == "fts":
                fts.append(hit)
            else:
                vector.append(hit)
        return MemorySearchChannels(fts=tuple(fts), vector=tuple(vector))

    async def vector_complete(
        self,
        connection: AsyncConnection,
        scope_id: str,
        memories: tuple[ArtifactRef, ...],
        profile: EmbeddingProfile,
        /,
    ) -> bool:
        return await self._vector.vector_complete(connection, scope_id, memories, profile)

    async def hydrate(
        self,
        connection: AsyncConnection,
        scope_id: str,
        projections: tuple[MemoryProjection, ...],
        /,
    ) -> tuple[MemoryProjection, ...]:
        return await self._vector.hydrate(connection, scope_id, projections)


__all__ = ["SeekDBMemoryIndex"]
