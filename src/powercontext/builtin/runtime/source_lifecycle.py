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

"""Propagate Source availability to dependent Memory and Topic Memory.

Each Scope owns an explicit dependency graph registered by the caller. A
withdrawal or restore event changes one Source, then every derivative is
re-evaluated under its own ``all`` / ``any`` policy. Registered bindings and
their resolved states are persisted, so a restarted Runtime answers from the
same graph, and a failed Memory state write can be retried without creating a
new Memory revision on a successful replay.
"""

# The service boundary raises one domain error with a caller-readable explanation;
# keeping those messages next to each contract check is clearer than a code table.
# ruff: noqa: TRY003, TRY301

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryCitation, MemoryService
from powercontext.builtin.artifacts.topic_memory import TopicMemory
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import (
    MAX_SOURCE_LIFECYCLE_NODE_ID_LENGTH,
    SOURCE_LIFECYCLE_NODES_TABLE,
)
from powercontext.builtin.sources import validate_scope_id
from powercontext.errors import ArtifactNotFoundError
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime

LifecycleKind = Literal["source", "memory", "topic"]
LifecyclePolicy = Literal["all", "any"]
LifecyclePurpose = Literal["generation", "audit"]

_MAX_NODE_ID_LENGTH = MAX_SOURCE_LIFECYCLE_NODE_ID_LENGTH
_SOURCE = "source"
_MEMORY = "memory"
_TOPIC = "topic"
_POLICIES = frozenset({"all", "any"})
_PURPOSES = frozenset({"generation", "audit"})
_MEMORY_REASON = "source-lifecycle"
_JSON_SEPARATORS = (",", ":")


class SourceLifecycleError(ValueError):
    """A lifecycle registration, event, or resolution violated its contract."""


class SourceLifecycleEvent(BaseModel):
    """One ordered availability change for an already registered Source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: StrictStr
    revision: StrictInt = Field(gt=0)
    sequence: StrictInt = Field(gt=0)
    available: StrictBool
    reason: StrictStr

    @field_validator("node_id", "reason")
    @classmethod
    def require_visible_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be a non-empty string")
        return value


@dataclass(frozen=True)
class _LifecycleNode:
    node_id: str
    kind: LifecycleKind
    reference: bytes
    parents: tuple[str, ...]
    policy: LifecyclePolicy
    available: bool
    source_revision: int | None
    source_sequence: int | None
    event_digest: str | None


class LifecycleApplication:
    """Select the Source lifecycle service for one Scope."""

    def __init__(self, runtime: BuiltinRuntime, database: AsyncDatabase | None) -> None:
        self._runtime = runtime
        self._database = database

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycle:
        return ScopedSourceLifecycle(self._runtime, self._database, scope_id)


class ScopedSourceLifecycle:
    """Register one evidence DAG and apply Source withdrawal or restore events."""

    def __init__(self, runtime: BuiltinRuntime, database: AsyncDatabase | None, scope_id: str) -> None:
        self._runtime = runtime
        self._database = database
        self.scope_id = validate_scope_id(scope_id)

    async def register_source(
        self,
        node_id: str,
        source_ref: SourceRef,
        /,
        revision: int = 1,
    ) -> None:
        """Bind one captured Source as a root with its lifecycle version."""

        reference = _coerce_reference(SourceRef, source_ref, "source_ref")
        _require_node_id(node_id)
        _require_positive_int(revision, "revision")
        await self._register(node_id, _SOURCE, reference, parents=(), policy="all", revision=revision)

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        /,
        *,
        parents: tuple[str, ...] | list[str],
        policy: str,
    ) -> None:
        """Bind one Memory entry version to the nodes that must support it."""

        reference = _coerce_reference(MemoryCitation, citation, "citation")
        _require_node_id(node_id)
        await self._register(node_id, _MEMORY, reference, parents=parents, policy=policy, revision=None)

    async def register_topic(
        self,
        node_id: str,
        artifact_ref: ArtifactRef,
        /,
        *,
        parents: tuple[str, ...] | list[str],
        policy: str,
    ) -> None:
        """Bind one Topic Memory revision to the nodes that must support it."""

        reference = _coerce_reference(ArtifactRef, artifact_ref, "artifact_ref")
        if reference.family != TopicMemory.family:
            raise SourceLifecycleError("topic reference must belong to the topic-memory family")
        _require_node_id(node_id)
        await self._register(node_id, _TOPIC, reference, parents=parents, policy=policy, revision=None)

    async def apply(self, event: SourceLifecycleEvent, /) -> list[str]:
        """Apply one Source event and return every currently available node, sorted."""

        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError("event must be a SourceLifecycleEvent")
        digest = _event_digest(event)
        async with self._runtime._scoped_operation(self.scope_id):
            async with self._require_database().transaction() as connection:
                nodes = await _load_nodes(connection, self.scope_id)
                source = nodes.get(event.node_id)
                if source is None or source.kind != _SOURCE:
                    raise SourceLifecycleError("event target is not a registered Source")
                if source.source_revision != event.revision:
                    raise SourceLifecycleError("event revision does not match the Source lifecycle version")
                stored_sequence = source.source_sequence
                if stored_sequence is not None:
                    if event.sequence < stored_sequence:
                        raise SourceLifecycleError("event sequence is older than the applied event")
                    if event.sequence == stored_sequence and source.event_digest != digest:
                        raise SourceLifecycleError("event sequence was reused with different content")
                if stored_sequence is None or event.sequence > stored_sequence:
                    nodes[event.node_id] = replace(
                        source,
                        available=event.available,
                        source_sequence=event.sequence,
                        event_digest=digest,
                    )
                    await connection.execute(
                        update(SOURCE_LIFECYCLE_NODES_TABLE)
                        .where(
                            SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id,
                            SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == event.node_id,
                        )
                        .values(
                            available=event.available,
                            source_sequence=event.sequence,
                            event_digest=digest,
                        )
                    )
                active = _available_node_ids(nodes)
                await _persist_propagation(connection, self.scope_id, nodes, active)
            await self._sync_memory_nodes(nodes)
        return sorted(active)

    async def resolve(self, node_id: str, /, *, purpose: LifecyclePurpose) -> SourceRef | MemoryCitation | ArtifactRef:
        """Return the original binding when its availability permits the purpose."""

        if purpose not in _PURPOSES:
            raise SourceLifecycleError("purpose must be 'generation' or 'audit'")
        _require_node_id(node_id)
        async with self._runtime._scoped_operation(self.scope_id):  # noqa: SIM117
            async with self._require_database().transaction() as connection:
                node = (await _load_nodes(connection, self.scope_id)).get(node_id)
        if node is None:
            raise SourceLifecycleError("unknown lifecycle node")
        if purpose == "generation" and not node.available:
            raise SourceLifecycleError("lifecycle node is not available for generation")
        return _reference_model(node)

    async def blocked_topic_artifact_ids(self) -> frozenset[str]:
        """Return Topic artifact identities that must leave native search and Context."""

        database = self._database
        if database is None:
            # A Runtime assembled without persistence has no registered lifecycle
            # bindings, so search must keep its ordinary behavior.
            return frozenset()
        async with database.transaction() as connection:
            rows = (
                await connection.execute(
                    select(SOURCE_LIFECYCLE_NODES_TABLE.c.reference).where(
                        SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id,
                        SOURCE_LIFECYCLE_NODES_TABLE.c.kind == _TOPIC,
                        SOURCE_LIFECYCLE_NODES_TABLE.c.available.is_(False),
                    )
                )
            ).mappings()
            blocked: set[str] = set()
            for row in rows:
                try:
                    reference = ArtifactRef.model_validate(json.loads(row["reference"]))
                except Exception as error:
                    raise SourceLifecycleError("stored Topic binding is invalid") from error
                blocked.add(reference.artifact_id)
            return frozenset(blocked)

    async def _register(
        self,
        node_id: str,
        kind: LifecycleKind,
        reference: SourceRef | MemoryCitation | ArtifactRef,
        *,
        parents: tuple[str, ...] | list[str],
        policy: str,
        revision: int | None,
    ) -> None:
        normalized_parents = _normalize_parents(parents)
        if kind == _SOURCE:
            if normalized_parents:
                raise SourceLifecycleError("a Source cannot declare parents")
            normalized_policy: LifecyclePolicy = "all"
        else:
            if not normalized_parents:
                raise SourceLifecycleError("a derivative node requires at least one parent")
            if policy not in _POLICIES:
                raise SourceLifecycleError("policy must be 'all' or 'any'")
            normalized_policy = policy  # type: ignore[assignment]
        payload = _dump_reference(reference)
        registered: _LifecycleNode | None = None
        async with self._runtime._scoped_operation(self.scope_id):  # noqa: SIM117
            async with self._require_database().transaction() as connection:
                nodes = await _load_nodes(connection, self.scope_id)
                existing = nodes.get(node_id)
                if existing is not None:
                    if not _same_binding(
                        existing,
                        kind,
                        payload,
                        normalized_parents,
                        normalized_policy,
                        revision,
                    ):
                        raise SourceLifecycleError("node identity is already bound to a different registration")
                    registered = existing
                else:
                    await self._validate_reference(kind, reference)
                    _validate_parents(node_id, normalized_parents, nodes)
                    available = _derived_availability(normalized_parents, normalized_policy, nodes)
                    registered = _LifecycleNode(
                        node_id=node_id,
                        kind=kind,
                        reference=payload,
                        parents=normalized_parents,
                        policy=normalized_policy,
                        available=available,
                        source_revision=revision,
                        source_sequence=None,
                        event_digest=None,
                    )
                    await connection.execute(
                        insert(SOURCE_LIFECYCLE_NODES_TABLE).values(
                            scope_id=self.scope_id,
                            node_id=node_id,
                            kind=kind,
                            reference=payload,
                            parents=_dump_parents(normalized_parents),
                            policy=normalized_policy,
                            available=available,
                            source_revision=revision,
                            source_sequence=None,
                            event_digest=None,
                        )
                    )
        if registered is not None and registered.kind == _MEMORY:
            await self._sync_memory_nodes({registered.node_id: registered})

    async def _validate_reference(
        self,
        kind: LifecycleKind,
        reference: SourceRef | MemoryCitation | ArtifactRef,
    ) -> None:
        if kind == _SOURCE:
            await self._validate_source(reference)
        elif kind == _MEMORY:
            await self._validate_memory(reference)
        else:
            await self._validate_topic(reference)

    async def _validate_source(self, reference: SourceRef | MemoryCitation | ArtifactRef) -> None:
        if not isinstance(reference, SourceRef):
            raise SourceLifecycleError("source_ref is not a SourceRef")
        async with self._runtime._context(self.scope_id) as context:
            try:
                sources = await context.sources.list()
                matched = next(
                    (source for source in sources if context.sources.catalog.as_ref(source) == reference),
                    None,
                )
                if matched is None:
                    raise SourceLifecycleError("registered Source does not exist in this Scope")
                await context.sources.read(matched)
            except SourceLifecycleError:
                raise
            except Exception as error:
                raise SourceLifecycleError("registered Source is not readable") from error

    async def _validate_memory(self, reference: SourceRef | MemoryCitation | ArtifactRef) -> None:
        if not isinstance(reference, MemoryCitation):
            raise SourceLifecycleError("citation is not a MemoryCitation")
        async with self._runtime._context(self.scope_id) as context:
            service = context.artifacts.memory
            try:
                memory = await service.revision(reference.memory_ref)
                if memory.artifact_id != context.artifacts.memory_artifact_id:
                    raise SourceLifecycleError("Memory citation belongs to another Scope")
                if not any(
                    item.entry_id == reference.entry_id and item.entry_version_id == reference.entry_version_id
                    for item in memory.content.manifest.entries
                ):
                    raise SourceLifecycleError("Memory citation does not identify a version in its Revision")
                await service.validate_citation(reference)
            except SourceLifecycleError:
                raise
            except Exception as error:
                raise SourceLifecycleError("Memory citation is not readable") from error

    async def _validate_topic(self, reference: SourceRef | MemoryCitation | ArtifactRef) -> None:
        if not isinstance(reference, ArtifactRef):
            raise SourceLifecycleError("artifact_ref is not an ArtifactRef")
        try:
            await self._runtime._records().get_artifact_revision(
                self.scope_id,
                reference.family,
                reference.artifact_id,
                reference.revision,
            )
        except Exception as error:
            raise SourceLifecycleError("Topic artifact revision is not readable") from error

    async def _sync_memory_nodes(self, nodes: dict[str, _LifecycleNode]) -> None:
        memory_nodes = [node for node in nodes.values() if node.kind == _MEMORY]
        if not memory_nodes:
            return
        async with (
            self._runtime._context(self.scope_id) as context,
            self._runtime._locked(self.scope_id),
        ):
            service = context.artifacts.memory
            memory_artifact_id = context.artifacts.memory_artifact_id
            for node in memory_nodes:
                await _sync_memory_node(service, memory_artifact_id, node)

    def _require_database(self) -> AsyncDatabase:
        if self._database is None:
            raise SourceLifecycleError("Source lifecycle requires a database-backed Runtime")
        return self._database


async def _load_nodes(connection: AsyncConnection, scope_id: str) -> dict[str, _LifecycleNode]:
    rows = (
        await connection.execute(
            select(SOURCE_LIFECYCLE_NODES_TABLE).where(SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == scope_id)
        )
    ).mappings()
    return {str(row["node_id"]): _row_node(row) for row in rows}


async def _persist_propagation(
    connection: AsyncConnection,
    scope_id: str,
    nodes: dict[str, _LifecycleNode],
    active: set[str],
) -> None:
    for node in nodes.values():
        available = node.node_id in active
        if node.available == available:
            continue
        nodes[node.node_id] = replace(node, available=available)
        await connection.execute(
            update(SOURCE_LIFECYCLE_NODES_TABLE)
            .where(
                SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == scope_id,
                SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == node.node_id,
            )
            .values(available=available)
        )


def _row_node(row: Mapping[str, object]) -> _LifecycleNode:
    node_id = str(row["node_id"])
    kind = str(row["kind"])
    if kind not in {_SOURCE, _MEMORY, _TOPIC}:
        raise SourceLifecycleError("stored lifecycle node has an unsupported kind")
    policy = str(row["policy"])
    if policy not in _POLICIES:
        raise SourceLifecycleError("stored lifecycle node has an unsupported policy")
    parents = json.loads(row["parents"])  # type: ignore[arg-type]
    if not isinstance(parents, list) or not all(isinstance(parent, str) for parent in parents):
        raise SourceLifecycleError("stored lifecycle node has invalid parents")
    revision = row["source_revision"]
    sequence = row["source_sequence"]
    digest = row["event_digest"]
    return _LifecycleNode(
        node_id=node_id,
        kind=kind,  # type: ignore[arg-type]
        reference=bytes(row["reference"]),  # type: ignore[arg-type]
        parents=tuple(parents),
        policy=policy,  # type: ignore[arg-type]
        available=bool(row["available"]),
        source_revision=None if revision is None else int(revision),
        source_sequence=None if sequence is None else int(sequence),
        event_digest=None if digest is None else str(digest),
    )


def _available_node_ids(nodes: dict[str, _LifecycleNode]) -> set[str]:
    state = {node_id: node.available for node_id, node in nodes.items() if node.kind == _SOURCE}
    pending = {node_id for node_id, node in nodes.items() if node.kind != _SOURCE}
    while pending:
        progressed = False
        for node_id in tuple(pending):
            node = nodes[node_id]
            if not all(parent in nodes for parent in node.parents):
                continue
            values = [state.get(parent) for parent in node.parents]
            if any(value is None for value in values):
                continue
            state[node_id] = all(values) if node.policy == "all" else any(values)
            pending.remove(node_id)
            progressed = True
        if not progressed:
            for node_id in pending:
                state[node_id] = False
            break
    return {node_id for node_id, available in state.items() if available}


def _derived_availability(
    parents: tuple[str, ...],
    policy: LifecyclePolicy,
    nodes: dict[str, _LifecycleNode],
) -> bool:
    values = [nodes[parent].available for parent in parents]
    return all(values) if policy == "all" else any(values)


def _validate_parents(node_id: str, parents: tuple[str, ...], nodes: dict[str, _LifecycleNode]) -> None:
    if len(set(parents)) != len(parents):
        raise SourceLifecycleError("parents must not repeat")
    for parent in parents:
        if parent == node_id:
            raise SourceLifecycleError("a node cannot depend on itself")
        if parent not in nodes:
            raise SourceLifecycleError("parent must be registered before its derivative")
    _ensure_acyclic(node_id, parents, nodes)


def _ensure_acyclic(node_id: str, parents: tuple[str, ...], nodes: dict[str, _LifecycleNode]) -> None:
    seen: set[str] = set()

    def visit(current: str) -> None:
        if current == node_id:
            raise SourceLifecycleError("registration would create a dependency cycle")
        if current in seen:
            return
        seen.add(current)
        for parent in nodes[current].parents:
            visit(parent)

    for parent in parents:
        visit(parent)


def _same_binding(
    existing: _LifecycleNode,
    kind: LifecycleKind,
    reference: bytes,
    parents: tuple[str, ...],
    policy: LifecyclePolicy,
    revision: int | None,
) -> bool:
    return (
        existing.kind == kind
        and existing.reference == reference
        and existing.parents == parents
        and existing.policy == policy
        and existing.source_revision == revision
    )


async def _sync_memory_node(service: MemoryService, memory_artifact_id: str, node: _LifecycleNode) -> None:
    try:
        reference = MemoryCitation.model_validate(json.loads(node.reference))
        head = await service.head(memory_artifact_id)
    except ArtifactNotFoundError as error:
        raise SourceLifecycleError("registered Memory is not readable") from error
    if reference.memory_ref.artifact_id != memory_artifact_id:
        raise SourceLifecycleError("registered Memory citation belongs to another Scope")
    manifest_entry = next(
        (item for item in head.content.manifest.entries if item.entry_id == reference.entry_id),
        None,
    )
    if manifest_entry is None:
        raise SourceLifecycleError("registered Memory entry no longer exists")
    desired_state = "active" if node.available else "inactive"
    if manifest_entry.state == desired_state:
        return
    current = next((item for item in await service.entries(head) if item.entry_id == reference.entry_id), None)
    if current is None:
        raise SourceLifecycleError("registered Memory entry no longer exists")
    if desired_state == "active":
        await service.reactivate(head, entries=(current,), reason=_MEMORY_REASON)
    else:
        await service.forget(head, entries=(current,), reason=_MEMORY_REASON)


def _reference_model(node: _LifecycleNode) -> SourceRef | MemoryCitation | ArtifactRef:
    payload = json.loads(node.reference)
    if node.kind == _SOURCE:
        return SourceRef.model_validate(payload)
    if node.kind == _MEMORY:
        return MemoryCitation.model_validate(payload)
    return ArtifactRef.model_validate(payload)


def _coerce_reference(model: type[SourceRef] | type[MemoryCitation] | type[ArtifactRef], value: object, name: str):
    if isinstance(value, model):
        return value
    try:
        return model.model_validate(value)
    except Exception as error:
        raise SourceLifecycleError(f"{name} is not a valid binding") from error


def _dump_reference(reference: SourceRef | MemoryCitation | ArtifactRef) -> bytes:
    return json.dumps(reference.model_dump(mode="json"), sort_keys=True, separators=_JSON_SEPARATORS).encode()


def _dump_parents(parents: tuple[str, ...]) -> bytes:
    return json.dumps(list(parents), separators=_JSON_SEPARATORS).encode()


def _event_digest(event: SourceLifecycleEvent) -> str:
    payload = json.dumps(
        {
            "revision": event.revision,
            "sequence": event.sequence,
            "available": event.available,
            "reason": event.reason,
        },
        sort_keys=True,
        separators=_JSON_SEPARATORS,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _require_node_id(node_id: object) -> None:
    if not isinstance(node_id, str) or not node_id.strip():
        raise SourceLifecycleError("node_id must be a non-empty string")
    if len(node_id) > _MAX_NODE_ID_LENGTH:
        raise SourceLifecycleError("node_id is too long")
    if node_id != node_id.strip():
        raise SourceLifecycleError("node_id must not contain surrounding whitespace")


def _require_positive_int(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SourceLifecycleError(f"{name} must be a positive integer")


def _normalize_parents(parents: object) -> tuple[str, ...]:
    if isinstance(parents, str) or not isinstance(parents, tuple | list):
        raise SourceLifecycleError("parents must be a sequence of node identifiers")
    normalized: list[str] = []
    for parent in parents:
        if not isinstance(parent, str) or not parent.strip() or parent != parent.strip():
            raise SourceLifecycleError("parents must be non-empty node identifiers")
        normalized.append(parent)
    return tuple(normalized)


__all__ = [
    "LifecycleApplication",
    "ScopedSourceLifecycle",
    "SourceLifecycleError",
    "SourceLifecycleEvent",
]
