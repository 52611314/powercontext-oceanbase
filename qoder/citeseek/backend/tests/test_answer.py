from citeseek.answer import select_bullets
from citeseek.export import answer_markdown, bibtex_for, hits_markdown


def _hit(text: str, chunk_id: str = "c1", page: int = 1) -> dict:
    return {
        "chunk_id": chunk_id,
        "doc_id": "d1",
        "doc_title": "示例讲义",
        "page": page,
        "section": "二、显存开销",
        "text": text,
        "score": 0.03,
        "channels": {"fts": {"rank": 1}},
    }


def test_select_bullets_picks_relevant_sentence():
    text = "显存占用可以通过 KV cache 压缩降低整体开销。今天天气很好。混合检索融合两路候选。"
    bullets = select_bullets([_hit(text)], "显存占用怎么降低")
    assert bullets
    assert "显存" in bullets[0]["text"]
    assert bullets[0]["citation"]["page"] == 1
    assert bullets[0]["citation"]["section"] == "二、显存开销"


def test_select_bullets_empty_without_match():
    assert select_bullets([_hit("今天天气很好，适合出门散步。")], "显存占用") == []


def test_select_bullets_limits_per_chunk():
    text = (
        "显存占用可以通过 KV cache 压缩显著降低整体开销。"
        "分页注意力能够减少显存占用并缓解内存碎片。"
        "量化方法把缓存精度降到八位从而节省显存占用。"
        "窗口淘汰只保留最近令牌，能快速降低显存占用。"
        "低秩分解把键值投影到低维空间，减少显存占用。"
    )
    bullets = select_bullets([_hit(text)], "显存占用", max_bullets=5, max_per_chunk=2)
    assert 1 <= len(bullets) <= 2


def test_select_bullets_dedupes_across_chunks():
    text = "显存占用可以通过 KV cache 压缩显著降低整体开销。"
    bullets = select_bullets(
        [_hit(text, "c1"), _hit(text, "c2")], "显存占用", max_bullets=5, max_per_chunk=2
    )
    assert len(bullets) == 1


def test_bibtex_escapes_and_contains_metadata():
    document = {
        "id": "a" * 32,
        "title": "A {B} \\ C",
        "authors": "张三",
        "year": 2024,
        "page_count": 2,
    }
    bib = bibtex_for(document)
    assert "@misc{citeseeka" in bib
    assert "\\{B\\}" in bib
    assert "year = {2024}" in bib
    assert "author = {张三}" in bib


def test_answer_markdown_contains_citations():
    bullets = [
        {
            "text": "结论句。",
            "citation": {"doc_title": "示例讲义", "page": 2, "section": "三、压缩"},
        }
    ]
    markdown = answer_markdown("显存怎么降低？", bullets)
    assert "## 出处" in markdown
    assert "《示例讲义》 第 2 页 · 三、压缩" in markdown
    assert "结论句。 [^1]" in markdown


def test_hits_markdown_lists_channels():
    markdown = hits_markdown(
        "混合检索",
        [
            {
                "doc_title": "示例讲义",
                "page": 1,
                "section": "四、RRF",
                "snippet": "RRF 只使用排名。",
                "score": 0.032,
                "channels": {"fts": {}, "vector": {}},
            }
        ],
    )
    assert "《示例讲义》 第 1 页 · 四、RRF" in markdown
    assert "通道: fts, vector" in markdown
