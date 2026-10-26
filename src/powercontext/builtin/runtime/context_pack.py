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

"""Serialize full prepared-context packs and prefix-reusing deltas.

The wire contract is deliberately small and strict: canonical JSON, standard Base64 chunks,
SHA-256 digests over the unsigned payload, and a self-consistent ``byte_count``. A receiver
with only the previous pack and the current delta can rebuild the exact prepared content.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

PACK_SCHEMA = "powercontext.context-pack.v1"
DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
_DIGEST_PREFIX = "sha256:"
_HEX_DIGITS = frozenset("0123456789abcdef")
_PACK_FIELDS = frozenset({"schema", "items", "digest", "byte_count"})
_DELTA_FIELDS = frozenset({"schema", "base_digest", "target_digest", "prefix_items", "appended"})
_SIZE_CONVERGENCE_LIMIT = 8


class ContextPackError(ValueError):
    """A context pack or delta failed its protocol, digest, or chunk validation."""


@dataclass(frozen=True)
class ContextPack:
    """One complete, immutable context payload split into UTF-8-safe chunks."""

    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    @classmethod
    def build(cls, items: Sequence[bytes]) -> ContextPack:
        chunks = _validated_chunks(items)
        digest = _digest(chunks)
        return cls(items=chunks, digest=digest, byte_count=_pack_size(chunks, digest))

    @classmethod
    def from_content(cls, content: str, *, chunk_bytes: int = 512) -> ContextPack:
        """Split exact UTF-8 content into deterministic chunks no larger than the limit."""

        if not isinstance(content, str):
            raise ContextPackError("content must be text")  # noqa: TRY003
        if not _is_int(chunk_bytes) or chunk_bytes <= 0:
            raise ContextPackError("chunk_bytes must be a positive integer")  # noqa: TRY003
        return cls.build(_split_utf8(content.encode("utf-8"), chunk_bytes))

    def to_bytes(self) -> bytes:
        return _encode_pack(self.items, self.digest, self.byte_count)

    @classmethod
    def from_bytes(cls, payload: bytes) -> ContextPack:
        document = _parse_object(payload, _PACK_FIELDS)
        if document["schema"] != PACK_SCHEMA:
            raise ContextPackError("unexpected pack schema")  # noqa: TRY003
        items = _decode_items(document["items"])
        digest = _require_digest(document["digest"], "digest")
        byte_count = _require_int(document["byte_count"], "byte_count")
        if digest != _digest(items):
            raise ContextPackError("pack digest mismatch")  # noqa: TRY003
        if _encode_pack(items, digest, byte_count) != bytes(payload):
            raise ContextPackError("pack is not canonical or byte_count is wrong")  # noqa: TRY003
        return cls(items=items, digest=digest, byte_count=byte_count)


@dataclass(frozen=True)
class ContextPackDelta:
    """A prefix-reusing update from one pack to another."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        """Keep the identical leading chunks and carry the whole target suffix."""

        if not isinstance(base, ContextPack) or not isinstance(target, ContextPack):
            raise ContextPackError("base and target must be context packs")  # noqa: TRY003
        prefix_items = 0
        for base_item, target_item in zip(base.items, target.items, strict=False):
            if base_item != target_item:
                break
            prefix_items += 1
        return cls(
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=prefix_items,
            appended=target.items[prefix_items:],
        )

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        return _canonical({
            "schema": DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": [base64.b64encode(item).decode("ascii") for item in self.appended],
        })

    @classmethod
    def from_bytes(cls, payload: bytes) -> ContextPackDelta:
        document = _parse_object(payload, _DELTA_FIELDS)
        if document["schema"] != DELTA_SCHEMA:
            raise ContextPackError("unexpected delta schema")  # noqa: TRY003
        return cls(
            base_digest=_require_digest(document["base_digest"], "base_digest"),
            target_digest=_require_digest(document["target_digest"], "target_digest"),
            prefix_items=_require_int(document["prefix_items"], "prefix_items"),
            appended=_decode_items(document["appended"]),
        )


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    """Rebuild the target pack from a base pack and a delta, or fail the protocol."""

    if not isinstance(base, ContextPack) or not isinstance(delta, ContextPackDelta):
        raise ContextPackError("base must be a pack and delta must be a delta")  # noqa: TRY003
    if delta.base_digest != base.digest:
        raise ContextPackError("delta base digest does not match the pack")  # noqa: TRY003
    if not _is_int(delta.prefix_items) or not 0 <= delta.prefix_items <= len(base.items):
        raise ContextPackError("delta prefix_items is out of range")  # noqa: TRY003
    target = ContextPack.build((*base.items[: delta.prefix_items], *delta.appended))
    if target.digest != delta.target_digest:
        raise ContextPackError("delta target digest does not match the rebuilt pack")  # noqa: TRY003
    return target


def _split_utf8(data: bytes, limit: int) -> tuple[bytes, ...]:
    if not data:
        return ()
    chunks: list[bytes] = []
    start = 0
    while start < len(data):
        end = min(start + limit, len(data))
        if end < len(data):
            while end > start and data[end] & 0xC0 == 0x80:
                end -= 1
            if end == start:
                raise ContextPackError("chunk_bytes cannot hold one UTF-8 character")  # noqa: TRY003
        chunks.append(data[start:end])
        start = end
    return tuple(chunks)


def _validated_chunks(items: Sequence[bytes]) -> tuple[bytes, ...]:
    if isinstance(items, (str, bytes, bytearray)):
        raise ContextPackError("items must be a sequence of byte chunks")  # noqa: TRY003
    chunks = tuple(items)
    for item in chunks:
        if not isinstance(item, bytes):
            raise ContextPackError("every pack item must be bytes")  # noqa: TRY003
    return chunks


def _digest(items: Sequence[bytes]) -> str:
    unsigned = {
        "schema": PACK_SCHEMA,
        "items": [base64.b64encode(item).decode("ascii") for item in items],
    }
    return _DIGEST_PREFIX + hashlib.sha256(_canonical(unsigned)).hexdigest()


def _encode_pack(items: Sequence[bytes], digest: str, byte_count: int) -> bytes:
    return _canonical({
        "schema": PACK_SCHEMA,
        "items": [base64.b64encode(item).decode("ascii") for item in items],
        "digest": digest,
        "byte_count": byte_count,
    })


def _pack_size(items: Sequence[bytes], digest: str) -> int:
    """Find the byte_count that equals the serialized length of the pack that contains it."""

    size = 0
    for _ in range(_SIZE_CONVERGENCE_LIMIT):
        encoded = _encode_pack(items, digest, size)
        if len(encoded) == size:
            return size
        size = len(encoded)
    raise ContextPackError("pack byte_count did not converge")  # noqa: TRY003


def _canonical(document: dict[str, Any]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _parse_object(payload: bytes, fields: frozenset[str]) -> dict[str, Any]:
    if not isinstance(payload, (bytes, bytearray)):
        raise ContextPackError("payload must be bytes")  # noqa: TRY003
    try:
        document = json.loads(bytes(payload))
    except (ValueError, UnicodeDecodeError) as error:
        raise ContextPackError("payload is not valid JSON") from error  # noqa: TRY003
    if not isinstance(document, dict):
        raise ContextPackError("payload must be a JSON object")  # noqa: TRY003
    if set(document) != fields:
        raise ContextPackError("payload fields do not match the schema")  # noqa: TRY003
    return document


def _decode_items(value: Any) -> tuple[bytes, ...]:
    if not isinstance(value, list):
        raise ContextPackError("items must be an array")  # noqa: TRY003
    items: list[bytes] = []
    for entry in value:
        if not isinstance(entry, str):
            raise ContextPackError("every item must be a Base64 string")  # noqa: TRY003
        try:
            items.append(base64.b64decode(entry, validate=True))
        except (binascii.Error, ValueError) as error:
            raise ContextPackError("item is not valid Base64") from error  # noqa: TRY003
    return tuple(items)


def _require_digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.startswith(_DIGEST_PREFIX):
        raise ContextPackError(f"{field} must be a sha256 digest")  # noqa: TRY003
    hexdigest = value[len(_DIGEST_PREFIX) :]
    if len(hexdigest) != 64 or not set(hexdigest) <= _HEX_DIGITS:
        raise ContextPackError(f"{field} must be a sha256 digest")  # noqa: TRY003
    return value


def _require_int(value: Any, field: str) -> int:
    if not _is_int(value) or value < 0:
        raise ContextPackError(f"{field} must be a non-negative integer")  # noqa: TRY003
    return value


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
