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

"""Row-level captured Sources for offline CSV documents."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterator
from urllib.parse import quote

from pydantic import BaseModel, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

CSV_ROW_SOURCE_NAME = "csv-row"
CSV_ROW_SOURCE_VERSION = "1"

_IDENTITY_PREFIX = "csv:"


class CSVDocumentInput(BaseModel):
    """One complete offline CSV document to parse into row records."""

    document_id: str
    primary_key: str
    content: bytes


class CSVRowInput(BaseModel):
    """One CSV data record identified by its document and primary-key value."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @model_validator(mode="after")
    def validate_row_shape(self) -> CSVRowInput:
        if not self.columns:
            raise ValueError("row columns must not be empty")  # noqa: TRY003
        if len(self.columns) != len(self.values):
            raise ValueError("row values must align with its columns")  # noqa: TRY003
        return self


class CSVRowSource(Source):
    """Captured CSV row that can be cited as row-level evidence."""

    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @model_validator(mode="after")
    def validate_row_identity(self) -> CSVRowSource:
        if not isinstance(self.primary_key, str) or not self.primary_key.strip():
            raise ValueError("primary_key must be a non-empty string")  # noqa: TRY003
        expected = csv_row_source_id(self.document_id, self.primary_key_value)
        if self.source_id != expected:
            raise ValueError("source_id must match the CSV row identity")  # noqa: TRY003
        if self.name != self.source_id:
            raise ValueError("name must equal source_id")  # noqa: TRY003
        if len(self.columns) != len(self.values):
            raise ValueError("row values must align with its columns")  # noqa: TRY003
        return self


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    """Parse one complete CSV document into validated row records.

    The whole document is validated before anything is returned, so malformed
    input never yields a partial tuple of rows.
    """

    document_id = _validate_document_id(value.document_id)
    primary_key = _validate_primary_key(value.primary_key)
    records = _read_csv_records(_decode_csv_content(value.content))
    header = _read_header(records)
    return _read_rows(
        records,
        document_id=document_id,
        primary_key=primary_key,
        columns=tuple(header),
        key_index=_header_index(header, primary_key),
    )


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Build the stable, delimiter-safe identity of one CSV row."""

    document_id = _validate_document_id(document_id)
    if not isinstance(primary_key_value, str) or not primary_key_value.strip():
        raise ValueError("primary_key_value must be a non-empty string")  # noqa: TRY003
    identity = f"{_IDENTITY_PREFIX}{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"CSV row identity must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return identity


class CSVRowSourceAdapter:
    """Resolve and read row-level captured CSV Sources."""

    input_class = CSVRowInput
    name = CSV_ROW_SOURCE_NAME
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        source_id = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


class CSVRowTextEvidenceProjection:
    """Expose one CSV row as canonical text evidence without changing values."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = dict(zip(source.columns, source.values, strict=True))
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_NAME,
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


CSV_ROW_SOURCE_ADAPTER = CSVRowSourceAdapter()
CSV_ROW_SOURCE_DEFINITION = AdapterSourceDefinition(
    CSV_ROW_SOURCE_ADAPTER,
    version=CSV_ROW_SOURCE_VERSION,
    projections=(CSVRowTextEvidenceProjection(),),
)


def _validate_document_id(document_id: str) -> str:
    if not isinstance(document_id, str) or not document_id.strip():
        raise ValueError("document_id must be a non-empty string")  # noqa: TRY003
    if document_id != document_id.strip():
        raise ValueError("document_id must not contain leading or trailing whitespace")  # noqa: TRY003
    return document_id


def _validate_primary_key(primary_key: object) -> str:
    if not isinstance(primary_key, str) or not primary_key:
        raise ValueError("primary_key must not be empty")  # noqa: TRY003
    return primary_key


def _decode_csv_content(content: bytes) -> str:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("CSV content must be strict UTF-8") from error  # noqa: TRY003
    if not text:
        raise ValueError("CSV content must not be empty")  # noqa: TRY003
    return text


def _read_header(records: Iterator[list[str]]) -> list[str]:
    try:
        header = next(records)
    except StopIteration:
        raise ValueError("CSV document must start with a header record") from None  # noqa: TRY003
    except csv.Error as error:
        raise ValueError("CSV content is malformed") from error  # noqa: TRY003
    if not header:
        raise ValueError("CSV header must not be empty")  # noqa: TRY003
    for column in header:
        if not column or not column.strip():
            raise ValueError("CSV header fields must be non-empty and not blank")  # noqa: TRY003
    if len(set(header)) != len(header):
        raise ValueError("CSV header fields must be unique")  # noqa: TRY003
    return header


def _header_index(header: list[str], primary_key: str) -> int:
    try:
        return header.index(primary_key)
    except ValueError:
        raise ValueError("primary_key must match one full header column") from None  # noqa: TRY003


def _read_rows(
    records: Iterator[list[str]],
    *,
    document_id: str,
    primary_key: str,
    columns: tuple[str, ...],
    key_index: int,
) -> tuple[CSVRowInput, ...]:
    rows: list[CSVRowInput] = []
    seen_keys: set[str] = set()
    try:
        for record in records:
            if not record:
                continue
            if len(record) != len(columns):
                raise ValueError("CSV record width must match the header width")  # noqa: TRY003
            primary_key_value = record[key_index]
            if not primary_key_value.strip():
                raise ValueError("CSV primary-key values must be non-empty and not blank")  # noqa: TRY003
            if primary_key_value in seen_keys:
                raise ValueError("CSV primary-key values must be unique per document")  # noqa: TRY003
            seen_keys.add(primary_key_value)
            rows.append(
                CSVRowInput(
                    document_id=document_id,
                    primary_key=primary_key,
                    primary_key_value=primary_key_value,
                    columns=columns,
                    values=tuple(record),
                )
            )
    except csv.Error as error:
        raise ValueError("CSV content is malformed") from error  # noqa: TRY003
    return tuple(rows)


def _read_csv_records(text: str) -> Iterator[list[str]]:
    return csv.reader(io.StringIO(text, newline=""), strict=True)


__all__ = [
    "CSV_ROW_SOURCE_ADAPTER",
    "CSV_ROW_SOURCE_DEFINITION",
    "CSV_ROW_SOURCE_NAME",
    "CSV_ROW_SOURCE_VERSION",
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "CSVRowSourceAdapter",
    "CSVRowTextEvidenceProjection",
    "csv_row_source_id",
    "parse_csv_document",
]
