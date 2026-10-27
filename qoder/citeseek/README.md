# CiteSeek

把论文、课程笔记和实验记录一次性导入，用**语义 + 关键词 + 元数据**混合检索，每条结果都能回到原文那一页。

- 检索存储：seekdb（HNSW 向量索引 + IK 中文全文索引 + 标量过滤）
- 嵌入：BAAI/bge-small-zh-v1.5（512 维 ONNX，随仓库内置，离线可用）
- 后端：Python 3.12 + FastAPI
- 前端：React 18 + TypeScript + Vite 5 + Ant Design 5（构建产物由后端直接托管）

> 项目网站：<https://citeseek-ma9lrveqlz1.qoder.zone>（Qoder Sites，公开访问）
>
> 当前进度：资料库、混合检索、带引用问答、前端界面、检索评测与项目主页均已完成。

## 界面

| 页面 | 能力 |
| --- | --- |
| 文献库 | 拖拽上传、索引进度、标签与阅读状态、去重、重建、删除 |
| 混合检索 | 混合/全文/向量三种模式、文档与标签筛选、命中高亮、出处跳转、seekdb 证据抽屉 |
| 带引用问答 | 抽取式回答、逐句页码与字符区间引用、原文高亮定位、导出 Markdown、问答历史 |
| 文献详情 | 页文本阅读、引用高亮、原文 PDF 跳页、BibTeX 导出 |

## 检索评测

固定 21 条中文查询，Top-10，三种模式在同一索引上对比（2026-10-28，容器内）：

| 模式 | Hit@10 | MRR@10 | 章节召回@10 | p50 延迟 |
| --- | --- | --- | --- | --- |
| hybrid | 100.00% | 0.8190 | 97.62% | 5.2 ms |
| fts | 95.24% | 0.8270 | 95.24% | 4.5 ms |
| vector | 100.00% | 0.7159 | 100.00% | 4.5 ms |

逐条结果与复现方式见 [docs/检索评测.md](docs/检索评测.md)，原始数据在 `docs/evidence/retrieval-eval.json`。

## 文档

| 文档 | 内容 |
| --- | --- |
| [运行说明](docs/运行说明.md) | 环境要求、一键启动、导入示例、验收脚本、常见问题 |
| [验证样例](docs/验证样例.md) | 8 步验收流程与每步预期结果 |
| [设计](docs/设计.md) | 数据模型、检索链路、索引与切块策略 |
| [技术证据](docs/技术证据.md) | seekdb 索引、混合检索 SQL、EXPLAIN 与线上证据 |
| [检索评测](docs/检索评测.md) | 查询集、指标定义、逐条结果 |
| [Qoder 开发记录](docs/Qoder开发记录.md) | 各阶段任务、Qoder 承担的工作与复盘 |
| [开发记录素材](../开发记录/) | 9 张 Qoder 阶段截图与 11.5 秒计划录屏 |
| [使用效果](docs/使用效果.md) | 真实使用记录与反馈的收集方法 |
| [提交材料](docs/提交材料.md) | 交付清单、证明位置与提交前检查 |

## 快速开始（Docker）

```bash
cd deploy
docker compose up -d --build
```

启动后：

- 界面：<http://127.0.0.1:8080>
- API 文档：<http://127.0.0.1:8080/docs>
- 健康检查：<http://127.0.0.1:8080/api/ready>
- seekdb：`127.0.0.1:2881`（root，空密码）

导入随仓库提供的示例资料（需要 `httpx`）：

```bash
python -m pip install httpx
python scripts/verify_library.py http://127.0.0.1:8080
```

如需从 Markdown 重新生成示例 PDF（可选，需要 `reportlab`），先运行 `python scripts/make_samples.py`。

## 本地开发

需要 Python 3.11+ 和一个运行中的 seekdb（`docker run -p 2881:2881 oceanbase/seekdb:latest`）。

```bash
cd backend
pip install -e ".[dev]"
PYTHONPATH=src uvicorn citeseek.main:app --reload --port 8000
```

```bash
make test        # 单元测试
make verify      # 导入 3 份示例 PDF 并核对索引结果
make web         # 构建前端（后端会自动托管 frontend/dist）
make web-dev     # 前端开发服务器（/api 代理到 8000）
```

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/documents` | 上传 PDF / Markdown / TXT（SHA-256 去重） |
| GET | `/api/documents` | 列表，支持 `q`、`status`、`read_status`、`tag` |
| GET | `/api/documents/{id}` | 详情 |
| PATCH | `/api/documents/{id}` | 修改标题、标签、阅读状态 |
| DELETE | `/api/documents/{id}` | 删除文献与索引 |
| POST | `/api/documents/{id}/reindex` | 重建索引 |
| GET | `/api/documents/{id}/status` | 索引进度 |
| GET | `/api/documents/{id}/pages/{page}` | 页文本与章节 |
| POST | `/api/search` | 混合检索（`hybrid` / `fts` / `vector`），RRF 融合 |
| GET | `/api/searches` | 检索历史 |
| GET | `/api/debug/retrieval/{search_id}` | 本次检索的 SQL、EXPLAIN、通道统计 |
| POST | `/api/ask` | 抽取式问答，逐句带页码与字符区间引用 |
| GET | `/api/answers` | 回答历史 |
| GET | `/api/answers/{id}/export.md` | 回答导出为 Markdown |
| GET | `/api/searches/{id}/export.md` | 检索结果导出为 Markdown |
| GET | `/api/documents/{id}/file` | 原文文件（PDF 支持 `#page=N` 跳页） |
| GET | `/api/documents/{id}/citation.bib` | 导出 BibTeX |
| GET | `/api/ready` | seekdb 与嵌入模型就绪检查 |

## 目录

```
citeseek/
├── backend/src/citeseek/   # FastAPI 应用：解析、切块、嵌入、存储
├── backend/models/         # 内置 bge-small-zh-v1.5（int8 ONNX，24MB）
├── backend/tests/          # pytest
├── deploy/                 # Dockerfile 与 docker compose
├── samples/notes/*.md      # 中文示例资料（自撰）
├── samples/papers/*.pdf    # 由 notes 生成，可直接导入
├── site/                   # Qoder Sites 项目主页（纯静态，含真实界面截图）
└── scripts/                # 样本生成、验收脚本、技术探针
```

## 检索证据

每次检索都会留下可核对的记录：

- `POST /api/search` 返回两路通道的排名、原始分数与 RRF 分数；
- `POST /api/ask` 的每条结论都带有页码和页内字符区间，前端可直接高亮到原文那一句；
- `GET /api/debug/retrieval/{search_id}` 返回真实 SQL 与 EXPLAIN，`indexes` 字段标明是否命中全文/向量索引；
- `data/logs/retrieval.jsonl` 逐条追加查询、过滤条件、耗时与 Top5 命中；
- 也可以直接查库核对：

```bash
docker compose exec seekdb mysql -uroot -P2881 -e \
  "SELECT doc_id, page, LEFT(text, 40) FROM citeseek.chunks LIMIT 5"
```

## 技术说明

- seekdb 以**服务端模式**运行（MySQL 协议），评审可以用任意 MySQL 客户端直接查库核对。
- 中文全文索引使用 seekdb 内置的 IK 分词（`WITH PARSER ik`），不需要额外的分词服务。
- 嵌入模型随仓库分发（int8 量化版）。与 fp32 版本对比，检索排序完全一致，向量余弦相似度约 0.96。
- 文本文件（Markdown）按标题切分为「页」，PDF 使用真实页码，检索结果统一带页码与章节。
