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

"""Pi v3 session branch history captured as immutable Sources."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, JsonValue, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    AdapterSourceDefinition,
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
    TextEvidence,
)
from powercontext.sources.models import Source, SourceMaterialization

PI_EVENT_SOURCE_NAME = "pi-event"
PI_EVENT_SOURCE_VERSION = "1"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"
PI_SESSION_CONNECTOR_VERSION = "1"

_CHECKPOINT_VERSION = 1
_IDENTITY_PREFIX = "pi:"


class PiSessionInput(BaseModel):
    """One offline Pi v3 JSONL session and an optional selected leaf."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible Pi event retained verbatim with its original record."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    """Captured Pi event that can be cited as branch history evidence."""

    source_id: str
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]

    @model_validator(mode="after")
    def validate_event_identity(self) -> PiEventSource:
        expected = pi_event_source_id(self.session_id, self.event_id)
        if self.source_id != expected:
            raise ValueError("source_id must match the Pi event identity")  # noqa: TRY003
        if self.name != self.source_id:
            raise ValueError("name must equal source_id")  # noqa: TRY003
        return self


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Return visible events on the full ancestor chain of the selected leaf."""

    return _parse_document(value)[1]


def pi_event_source_id(session_id: str, event_id: str) -> str:
    """Build the stable, delimiter-safe identity of one Pi session event."""

    session_id = _validate_identity_part("session_id", session_id)
    event_id = _validate_identity_part("event_id", event_id)
    identity = f"{_IDENTITY_PREFIX}{quote(session_id, safe='')}:{quote(event_id, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"Pi event identity must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return identity


class PiEventSourceAdapter:
    """Resolve and read captured Pi event Sources."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        source_id = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class PiEventTextEvidenceProjection:
    """Expose one visible Pi event as canonical text evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.source_id,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


class PiSessionConnector:
    """Incrementally capture one selected branch of an offline Pi session."""

    name = PI_SESSION_CONNECTOR_NAME
    version = PI_SESSION_CONNECTOR_VERSION
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self._path = Path(path)
        self._leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        try:
            content = self._path.read_bytes()
        except OSError as error:
            raise ValueError("Pi session file cannot be read") from error  # noqa: TRY003
        session_id, events = _parse_document(PiSessionInput(content=content, leaf_id=self._leaf_id))
        previous = _load_checkpoint(session.checkpoint)
        imported: dict[str, str] = {}
        if previous is not None:
            previous_session_id, imported = previous
            if previous_session_id != session_id:
                raise ValueError("Pi session identity changed for this connector binding")  # noqa: TRY003
        digests = {event.event_id: _event_digest(event) for event in events}
        for event_id, digest in imported.items():
            current = digests.get(event_id)
            if current is not None and current != digest:
                raise ValueError("already imported Pi event was rewritten")  # noqa: TRY003
        for event in events:
            if event.event_id in imported:
                continue
            await session.submit(event.event_id, PI_EVENT_SOURCE_NAME, event)
            imported[event.event_id] = digests[event.event_id]
        checkpoint = {
            "version": _CHECKPOINT_VERSION,
            "session_id": session_id,
            "events": dict(sorted(imported.items())),
        }
        return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE, checkpoint=checkpoint)


PI_EVENT_SOURCE_ADAPTER = PiEventSourceAdapter()
PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(
    PI_EVENT_SOURCE_ADAPTER,
    version=PI_EVENT_SOURCE_VERSION,
    projections=(PiEventTextEvidenceProjection(),),
)


def _parse_document(value: PiSessionInput) -> tuple[str, tuple[PiEventInput, ...]]:
    records = _read_records(_decode_content(value.content))
    if not records:
        raise ValueError("Pi session must contain a session header")  # noqa: TRY003
    session_id = _read_session_header(records[0])
    by_id, order = _build_nodes(records[1:])
    chain = _leaf_chain(by_id, order, value.leaf_id)
    events = tuple(event for node in chain if (event := _visible_event(session_id, node)) is not None)
    return session_id, events


def _decode_content(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Pi session content must be strict UTF-8") from error  # noqa: TRY003


def _read_records(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError("Pi session lines must be valid JSON") from error  # noqa: TRY003
        if not isinstance(record, dict):
            raise ValueError("Pi session records must be JSON objects")  # noqa: TRY003, TRY004
        records.append(record)
    return records


def _read_session_header(record: dict[str, Any]) -> str:
    if record.get("type") != "session":
        raise ValueError("Pi session must start with a session header")  # noqa: TRY003
    version = record.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version != 3:
        raise ValueError("Pi session header must declare version 3")  # noqa: TRY003
    session_id = record.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("Pi session header id must be a non-empty string")  # noqa: TRY003
    return session_id


def _build_nodes(records: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for record in records:
        event_id = record.get("id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("Pi session records must have a non-empty string id")  # noqa: TRY003
        if event_id in by_id:
            raise ValueError("Pi session record ids must be unique")  # noqa: TRY003
        if "parentId" not in record:
            raise ValueError("Pi session records must declare parentId")  # noqa: TRY003
        parent_id = record["parentId"]
        if parent_id is not None:
            if not isinstance(parent_id, str) or not parent_id:
                raise ValueError("Pi session parentId must be null or a non-empty string")  # noqa: TRY003
            if parent_id not in by_id:
                raise ValueError("Pi session parentId must reference an earlier record")  # noqa: TRY003
        by_id[event_id] = record
        order.append(event_id)
    return by_id, order


def _leaf_chain(
    by_id: dict[str, dict[str, Any]],
    order: list[str],
    leaf_id: str | None,
) -> list[dict[str, Any]]:
    if not order:
        raise ValueError("Pi session must contain at least one event record")  # noqa: TRY003
    selected = order[-1] if leaf_id is None else leaf_id
    if selected not in by_id:
        raise ValueError("Pi session selected leaf does not exist")  # noqa: TRY003
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    current: str | None = selected
    while current is not None:
        if current in seen:
            raise ValueError("Pi session graph must not contain cycles")  # noqa: TRY003
        seen.add(current)
        record = by_id[current]
        chain.append(record)
        parent = record["parentId"]
        current = parent if isinstance(parent, str) else None
    chain.reverse()
    return chain


def _visible_event(session_id: str, node: dict[str, Any]) -> PiEventInput | None:
    event_type = node.get("type")
    if not isinstance(event_type, str) or not event_type:
        return None
    detail = _event_role_and_body(event_type, node)
    if detail is None:
        return None
    role, body = detail
    if not body:
        return None
    return PiEventInput(
        session_id=session_id,
        event_id=node["id"],
        parent_id=node["parentId"],
        event_type=event_type,
        role=role,
        content=f"{role}: {body}",
        record=node,
    )


def _event_role_and_body(event_type: str, node: dict[str, Any]) -> tuple[str, str] | None:
    if event_type == "message":
        message = node.get("message")
        return _message_body(message) if isinstance(message, dict) else None
    if event_type in {"compaction", "branch_summary"}:
        summary = node.get("summary")
        return (event_type, summary) if isinstance(summary, str) else None
    if event_type == "custom_message":
        return "custom", _visible_text(node.get("content"))
    return None


def _message_body(message: dict[str, Any]) -> tuple[str, str] | None:
    role = message.get("role")
    if not isinstance(role, str) or not role:
        return None
    if role == "toolResult":
        tool_name = _as_text(message.get("toolName"))
        call_id = _as_text(message.get("toolCallId"))
        return role, f"[tool_result {tool_name} {call_id}] {_visible_text(message.get('content'))}"
    if role == "bashExecution":
        return role, f"$ {_as_text(message.get('command'))}\n{_as_text(message.get('output'))}"
    content = message.get("content")
    if isinstance(content, str):
        return role, content
    if not isinstance(content, list):
        return None
    return role, "\n".join(_message_block_parts(content))


def _message_block_parts(blocks: list[Any]) -> list[str]:
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
        elif block_type == "toolCall":
            arguments = json.dumps(
                block.get("arguments"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            name = _as_text(block.get("name"))
            call_id = _as_text(block.get("id"))
            parts.append(f"[tool_call {name} {call_id}] {arguments}")
    return parts


def _visible_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _as_text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _validate_identity_part(field: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")  # noqa: TRY003
    return value


def _event_digest(event: PiEventInput) -> str:
    encoded = json.dumps(event.record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_checkpoint(value: JsonValue | None) -> tuple[str, dict[str, str]] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("version") != _CHECKPOINT_VERSION:
        raise ValueError("Pi connector checkpoint is not supported")  # noqa: TRY003
    session_id = value.get("session_id")
    events = value.get("events")
    if not isinstance(session_id, str) or not isinstance(events, dict):
        raise ValueError("Pi connector checkpoint is malformed")  # noqa: TRY003, TRY004
    imported: dict[str, str] = {}
    for event_id, digest in events.items():
        if not isinstance(event_id, str) or not isinstance(digest, str):
            raise ValueError("Pi connector checkpoint is malformed")  # noqa: TRY003, TRY004
        imported[event_id] = digest
    return session_id, imported


__all__ = [
    "PI_EVENT_SOURCE_ADAPTER",
    "PI_EVENT_SOURCE_DEFINITION",
    "PI_EVENT_SOURCE_NAME",
    "PI_EVENT_SOURCE_VERSION",
    "PI_SESSION_CONNECTOR_NAME",
    "PI_SESSION_CONNECTOR_VERSION",
    "PiEventInput",
    "PiEventSource",
    "PiEventSourceAdapter",
    "PiEventTextEvidenceProjection",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
    "pi_event_source_id",
]
