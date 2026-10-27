from citeseek.retrieval import _fuse, build_sql


def test_build_sql_hybrid_contains_both_channels():
    sql, params = build_sql("显存", "[0.1,0.2]", "hybrid", None, 20)
    assert "MATCH(c.text)" in sql
    assert "COSINE_DISTANCE(c.embedding" in sql
    assert "UNION ALL" in sql
    assert params == ["显存", "显存", 20, "[0.1,0.2]", 20]


def test_build_sql_pushes_filters_to_both_branches():
    sql, params = build_sql("显存", "[0.1]", "hybrid", ["doc-a", "doc-b"], 10)
    assert sql.count("c.doc_id IN (%s,%s)") == 2
    assert params == ["显存", "显存", "doc-a", "doc-b", 10, "[0.1]", "doc-a", "doc-b", 10]


def test_build_sql_empty_filter_matches_nothing():
    sql, params = build_sql("显存", "[0.1]", "hybrid", [], 10)
    assert sql.count("1=0") == 2
    assert params == ["显存", "显存", 10, "[0.1]", 10]


def test_build_sql_single_channel_modes():
    fts_sql, fts_params = build_sql("显存", "[0.1]", "fts", None, 5)
    assert "COSINE_DISTANCE" not in fts_sql
    assert fts_params == ["显存", "显存", 5]

    vec_sql, vec_params = build_sql("显存", "[0.1]", "vector", None, 5)
    assert "MATCH(" not in vec_sql
    assert vec_params == ["[0.1]", 5]


def _row(channel: str, chunk_id: str, score: float | None, dist: float | None) -> dict:
    return {
        "channel": channel,
        "id": chunk_id,
        "doc_id": "doc",
        "page": 1,
        "section": "s",
        "text": "t",
        "char_start": 0,
        "char_end": 1,
        "score": score,
        "dist": dist,
    }


def test_fuse_prefers_chunks_hit_by_both_channels():
    rows = [
        _row("fts", "a", 2.0, None),
        _row("fts", "b", 1.0, None),
        _row("vector", "b", None, 0.1),
        _row("vector", "c", None, 0.2),
    ]
    fused = _fuse(rows)
    assert [item["chunk_id"] for item in fused] == ["b", "a", "c"]
    assert set(fused[0]["channels"]) == {"fts", "vector"}
    assert fused[0]["score"] > fused[1]["score"]


def test_fuse_keeps_single_channel_ranks():
    rows = [_row("vector", "x", None, 0.3)]
    fused = _fuse(rows)
    assert fused[0]["channels"]["vector"]["rank"] == 1
    assert fused[0]["channels"]["vector"]["distance"] == 0.3
