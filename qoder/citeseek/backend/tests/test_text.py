from citeseek.text import (
    chunk_text,
    find_spans,
    merge_wrapped_lines,
    normalize,
    sections_in_page,
    snippet,
    split_sections,
    split_sentences,
)


def test_normalize_collapses_whitespace():
    assert normalize("a  b\r\n\r\n\r\nc") == "a b\n\nc"


def test_split_sentences_keeps_punctuation():
    sentences = split_sentences("第一句。第二句！Third?")
    assert sentences == ["第一句。", "第二句！", "Third?"]


def test_chunks_offsets_match_text():
    text = "索引结构决定了查询的代价。B+ 树适合范围扫描，哈希索引适合等值查询。" * 20
    chunks = chunk_text(text, target=200, overlap=40)
    assert len(chunks) > 3
    for chunk, start, end in chunks:
        assert text[start:end] == chunk
        assert len(chunk) <= 900


def test_adjacent_chunks_overlap():
    text = "第一句话介绍背景。第二句话说明方法。第三句话给出结论。" * 30
    chunks = chunk_text(text, target=120, overlap=60)
    assert len(chunks) >= 2
    assert chunks[1][1] < chunks[0][2], "相邻块应有字符重叠"


def test_short_page_single_chunk():
    chunks = chunk_text("只有一句话。", target=400, overlap=80)
    assert chunks == [("只有一句话。", 0, 6)]


def test_find_spans_returns_positions():
    text = "本文比较向量检索与混合检索的差异。"
    spans = find_spans(text, "混合检索")
    assert spans
    assert all(text[start:end] for start, end in spans)


def test_find_spans_handles_english_terms():
    text = "HNSW and BM25 are used in hybrid search."
    spans = find_spans(text, "HNSW BM25")
    words = {text[start:end].upper() for start, end in spans}
    assert {"HNSW", "BM25"} <= words


def test_query_terms_keep_symbol_terms_and_mixed_phrases():
    from citeseek.text import query_terms

    terms = set(query_terms("B+ 树和哈希索引"))
    assert "b+" in terms
    assert "b+ 树" in terms or "b+树" in terms


def test_sections_in_page_returns_offsets():
    text = "一、开头\n正文内容。\n二、第二节\n更多正文。"
    sections = sections_in_page(text)
    assert [title for _, title in sections] == ["一、开头", "二、第二节"]
    assert sections[1][0] == text.index("二、第二节")


def test_snippet_centers_on_longest_match():
    text = "铺垫" * 120 + "显存占用" + "收尾" * 120
    result = snippet(text, "显存占用", width=80)
    assert "显存占用" in result
    assert len(result) <= 82


def test_merge_wrapped_lines_joins_soft_wraps():
    text = "大模型的推理分为预填充阶段\n和解码阶段。\n二、KV cache 的开销\n自回归生成时"
    merged = merge_wrapped_lines(text)
    assert "预填充阶段和解码阶段。" in merged
    assert "\n二、KV cache 的开销\n" in merged


def test_split_sections_preserves_every_character():
    text = "前言。\n一、第一节\n内容一。\n二、第二节\n内容二。"
    sections = sections_in_page(text)
    segments = split_sections(text, sections, "前言")
    assert [title for _, title, _ in segments] == ["前言", "一、第一节", "二、第二节"]
    assert "".join(segment for _, _, segment in segments) == text
