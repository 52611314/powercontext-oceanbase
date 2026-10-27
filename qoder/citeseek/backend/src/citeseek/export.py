from __future__ import annotations

from typing import Any


def _escape_bib(value: str) -> str:
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def bibtex_for(document: dict[str, Any]) -> str:
    key = f"citeseek{document['id'][:8]}"
    fields: list[tuple[str, str]] = [
        ("title", document.get("title") or ""),
        ("author", document.get("authors") or ""),
        ("year", str(document["year"]) if document.get("year") else ""),
        ("note", f"Imported via CiteSeek; pages={document.get('page_count', 0)}"),
    ]
    body = ",\n".join(f"  {name} = {{{_escape_bib(value)}}}" for name, value in fields if value)
    return f"@misc{{{key},\n{body}\n}}\n"


def answer_markdown(question: str, bullets: list[dict[str, Any]]) -> str:
    lines = [f"# {question}", "", "## 结论", ""]
    if not bullets:
        lines.append("_没有找到足够相关的证据。_")
    for index, bullet in enumerate(bullets, start=1):
        lines.append(f"{index}. {bullet['text']} [^{index}]")
    if bullets:
        lines.extend(["", "## 出处", ""])
        for index, bullet in enumerate(bullets, start=1):
            citation = bullet["citation"]
            section = f" · {citation['section']}" if citation.get("section") else ""
            lines.append(
                f"[^{index}]: 《{citation['doc_title']}》 第 {citation['page']} 页{section}"
            )
    lines.extend(["", "---", "由 CiteSeek 基于 seekdb 混合检索生成。"])
    return "\n".join(lines) + "\n"


def hits_markdown(query: str, hits: list[dict[str, Any]]) -> str:
    lines = [f"# 检索：{query}", ""]
    if not hits:
        lines.append("_没有命中结果。_")
    for index, hit in enumerate(hits, start=1):
        section = f" · {hit['section']}" if hit.get("section") else ""
        channels = ", ".join(sorted(hit.get("channels", {})))
        lines.append(f"{index}. 《{hit['doc_title']}》 第 {hit['page']} 页{section}")
        lines.append(f"   > {hit.get('snippet', '')}")
        lines.append(f"   - RRF: {hit.get('score')} · 通道: {channels}")
        lines.append("")
    lines.extend(["---", "由 CiteSeek 基于 seekdb 混合检索生成。"])
    return "\n".join(lines) + "\n"
