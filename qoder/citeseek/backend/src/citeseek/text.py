from __future__ import annotations

import re

SENTENCE_RE = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?", re.S)
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
ASCII_TERM_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_\-\.\+#]{1,}")
HEADING_RE = re.compile(
    r"^(第[一二三四五六七八九十百]+[章节]|[0-9]+(\.[0-9]+){0,2}[ 、.]"
    r"|[一二三四五六七八九十]+、)\s*\S.{1,40}$"
)


def normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_sentences(text: str) -> list[str]:
    return [
        match.group(0).strip() for match in SENTENCE_RE.finditer(text) if match.group(0).strip()
    ]


def chunk_text(
    text: str, target: int = 420, overlap: int = 80, max_chars: int = 900
) -> list[tuple[str, int, int]]:
    """Sentence-aligned chunks that never cross the page boundary."""
    spans: list[tuple[int, int]] = []
    for match in SENTENCE_RE.finditer(text):
        if match.group(0).strip():
            spans.append((match.start(), match.end()))
    if not spans:
        return []

    chunks: list[tuple[str, int, int]] = []
    start_idx = 0
    while start_idx < len(spans):
        end_idx = start_idx
        length = 0
        while end_idx < len(spans):
            sentence_len = spans[end_idx][1] - spans[end_idx][0]
            if length and length + sentence_len > target * 1.6:
                break
            length += sentence_len
            end_idx += 1
            if length >= target:
                break
        start_char = spans[start_idx][0]
        end_char = spans[end_idx - 1][1]
        while start_char < end_char and text[start_char].isspace():
            start_char += 1
        while end_char > start_char and text[end_char - 1].isspace():
            end_char -= 1
        if end_char - start_char > max_chars:
            end_char = start_char + max_chars
        chunk = text[start_char:end_char]
        if chunk:
            chunks.append((chunk, start_char, end_char))
        if end_idx >= len(spans):
            break
        # Step back so the tail of this chunk overlaps the next one.
        overlap_start = end_idx
        kept = 0
        while overlap_start > start_idx + 1:
            candidate = spans[overlap_start - 1][1] - spans[overlap_start - 1][0]
            if kept + candidate > overlap:
                break
            kept += candidate
            overlap_start -= 1
        start_idx = max(overlap_start, start_idx + 1)
    return chunks


def query_terms(query: str) -> list[str]:
    terms: set[str] = {term.lower() for term in ASCII_TERM_RE.findall(query)}
    for match in re.finditer(r"([A-Za-z0-9][A-Za-z0-9_\-\.\+#]*)\s*([\u4e00-\u9fff]{1,2})", query):
        ascii_part = match.group(1).lower()
        cjk_part = match.group(2)
        for size in (1, 2):
            prefix = cjk_part[:size]
            terms.add(f"{ascii_part}{prefix}")
            terms.add(f"{ascii_part} {prefix}")
    cjk = "".join(CJK_RE.findall(query))
    for size in (2, 3, 4):
        for index in range(len(cjk) - size + 1):
            terms.add(cjk[index : index + size])
    return sorted(terms, key=len, reverse=True)


def find_spans(text: str, query: str, limit: int = 24) -> list[list[int]]:
    terms = [term for term in query_terms(query) if len(term) >= 2]
    if not terms or not text:
        return []
    pattern = re.compile("|".join(re.escape(term) for term in terms), re.IGNORECASE)
    spans = [[match.start(), match.end()] for match in pattern.finditer(text)][:limit]
    return spans


def snippet(text: str, query: str, width: int = 220) -> str:
    spans = find_spans(text, query, limit=12)
    if not spans:
        return text[:width] + ("…" if len(text) > width else "")
    best = max(spans, key=lambda span: span[1] - span[0])
    start = max(0, best[0] - width // 3)
    end = min(len(text), start + width)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + text[start:end] + suffix


def sections_in_page(text: str) -> list[tuple[int, str]]:
    sections: list[tuple[int, str]] = []
    cursor = 0
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped and looks_like_heading(stripped):
            index = text.find(stripped, cursor)
            if index >= 0:
                sections.append((index, stripped[:250]))
                cursor = index + len(stripped)
    return sections


def split_sections(
    text: str, sections: list[tuple[int, str]], fallback: str
) -> list[tuple[int, str, str]]:
    """Split a page into (start_offset, section_title, segment_text) without losing text."""
    if not text.strip():
        return []
    if not sections:
        return [(0, fallback, text)]
    segments: list[tuple[int, str, str]] = []
    first = sections[0][0]
    if first > 0 and text[:first].strip():
        segments.append((0, fallback, text[:first]))
    for index, (offset, title) in enumerate(sections):
        end = sections[index + 1][0] if index + 1 < len(sections) else len(text)
        segment = text[offset:end]
        if segment.strip():
            segments.append((offset, title, segment))
    return segments


def looks_like_heading(line: str) -> bool:
    return bool(HEADING_RE.match(line.strip()))


def merge_wrapped_lines(text: str) -> str:
    """Join PDF soft-wrapped lines so a sentence is not split by a line break."""
    merged: list[str] = []
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if not merged:
            merged.append(line)
            continue
        previous = merged[-1]
        ends_paragraph = bool(re.search(r"[。！？!?；;]$", previous))
        if ends_paragraph or looks_like_heading(line) or looks_like_heading(previous):
            merged.append(line)
        else:
            joiner = "" if not previous[-1].isascii() and not line[0].isascii() else " "
            merged[-1] = previous + joiner + line
    return "\n".join(merged)
