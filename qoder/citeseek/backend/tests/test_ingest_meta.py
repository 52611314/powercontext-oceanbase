from citeseek.ingest import ParsedPage, _guess_title, _guess_year
from citeseek.store import normalize_tags, tags_to_list


def test_guess_year_finds_recent_year():
    assert _guess_year("发表于 2024 年的论文") == 2024


def test_guess_year_returns_none_when_missing():
    assert _guess_year("没有年份的文本") is None


def test_guess_title_prefers_metadata():
    pages = [ParsedPage(1, "", "正文第一行\n第二行")]
    assert _guess_title(pages, "真正的标题", "file.pdf") == "真正的标题"


def test_guess_title_falls_back_to_first_line():
    pages = [ParsedPage(1, "", "数据库系统课程笔记\n作者：某某")]
    assert _guess_title(pages, "", "notes.pdf") == "数据库系统课程笔记"


def test_guess_title_falls_back_to_filename():
    pages = [ParsedPage(1, "", "短\nabc")]
    assert _guess_title(pages, "", "我的笔记.md") == "我的笔记"


def test_tags_roundtrip():
    raw = normalize_tags("检索, 向量检索，检索 , 评测")
    assert raw == ",检索,向量检索,评测,"
    assert tags_to_list(raw) == ["检索", "向量检索", "评测"]


def test_empty_tags():
    assert normalize_tags("") == ""
    assert tags_to_list("") == []
