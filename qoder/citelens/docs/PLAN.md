# CiteLens 实施计划

> 状态：立项版（2026-10-26）。本文件是 CiteLens 的开发基准，后续变更需同步更新。
> 赛题：Qoder × OceanBase「Build with Qoder」。提交分支 `2026-competition-ecosystem`，资产位于 `qoder/citelens/`。

## 1. 项目定义

**一句话**：把「读过就忘、找不到出处、重复读已掌握内容」的文献工作，变成一条可追溯的证据链。

| 项 | 内容 |
| --- | --- |
| 名称 | CiteLens（引用透镜） |
| 用户 | 研究生、本科生、科研工作者 |
| 核心场景 | 组会/开题前，把本周读的 5 篇论文整理成一页带页码出处的对比，并续上上次没懂的概念 |
| 差异化 | 每句结论可点回原文页码；系统记住阅读进度与薄弱概念；下次打开直接续读 |
| 不做 | 不做通用聊天机器人，不生成无出处的总结，不做扫描件 OCR |

**闭环**：导入 PDF → 页级解析 → 混合索引 → 提问/对比 → 带引用回答 → 标记未懂 → 写入记忆 → 下次续读 → 复习卡片。

## 2. 赛题验收对照

| 验收项 | CiteLens 的满足方式 |
| --- | --- |
| 网站可访问 | Qoder Sites 发布「任何人都可访问」，无痕窗口验证 |
| 项目能运行 | `docker compose up` 一键起（app + PowerContext），`docs/运行说明.md` + `docs/验证样例.md` |
| Qoder 开发可核对 | 每个里程碑一个 Qoder 会话，桌面版截图 + CLI transcript（`docs/qoder/`），附账号 UID |
| 技术结合有效 | seekdb：检索 SQL、EXPLAIN、`logs/retrieval.jsonl`；PowerContext：Dashboard、`logs/memory.jsonl` |

## 3. 功能规格 v1.0

| 模块 | 功能点 | 验收标准 |
| --- | --- | --- |
| 文献库 | 多文件上传、SHA-256 去重、元数据提取、状态（未读/在读/已读/精读）、标签、搜索排序筛选、删除、重新索引 | 上传 5 篇 arXiv PDF 全部成功；重复上传提示已存在；列表可按状态/标签筛选 |
| 解析索引 | pypdf 页级文本、章节启发式、句级切块（带重叠）、jieba 分词、512 维向量、HNSW + FULLTEXT | 500 页 PDF 索引 < 3 分钟；每块保留准确页码；索引进度实时可见 |
| 混合检索 | 全文 + 向量 + 元数据过滤单条 SQL、分数融合、调试面板（SQL/EXPLAIN/各路分数/耗时）、检索日志 | 无过滤时两路都命中；EXPLAIN 同时出现 `VECTOR INDEX` 与 `TEXT RETRIEVAL SCAN`；1000 块 p50 < 300ms |
| 问答 | 证据选择（IDF + 邻句合并）、抽取式回答、逐句引用、引用跳转、回答历史 | 3 个预设问题各 ≥3 条引用，页码与原文一致；每句都有引用 |
| 对比表 | 选 2–5 篇 + 主题，生成「方法/数据/结论/局限/可复现性」表，每格带出处，导出 Markdown/CSV | 5 篇对比表 < 30s；每个格子可点回原文；导出可打开 |
| 阅读器 | pdf.js 渲染、页码跳转、引用高亮、选中文本标「未懂」、阅读进度自动记录 | 引用跳转正确页并高亮；标记后记忆面板立即出现 |
| 记忆 | PowerContext Atomic Memory 存画像/薄弱概念/阅读进度/历史问题/偏好；首页续读；记忆面板 CRUD；多 profile；`prepare_context` 注入 | 关掉重开显示上次进度与 2 个未懂概念；Dashboard 可见记录 |
| 复习 | 简化 SM-2、复习卡片（概念 + 出处 + 相关论文）、评分、到期提醒 | 标记后生成卡片；评分后到期时间变化 |
| 统计 | 事件埋点、仪表盘（天数/文献/问答/复习/耗时）、提效计时器、JSON/CSV 导出 | 数字与实际操作一致；导出可用于提交 |
| 设置 | Embedding provider（本地/OpenAI 兼容）、可选 LLM、数据目录、重建索引 | 切换 provider 后重建成功；无 key 默认路径可用 |
| 体验 | 中文界面、响应式、加载/错误/空状态 | 1280 与 1920 宽度正常 |

## 4. 技术架构

### 4.1 选型

| 层 | 选型 | 理由 |
| --- | --- | --- |
| 后端 | Python 3.12 + FastAPI + Pydantic v2 | 与 PowerContext 同语言，自动 OpenAPI |
| 检索存储 | seekdb（`pylibseekdb` 嵌入式） | HNSW + FULLTEXT + 标量过滤，一条 SQL；EXPLAIN 可作证据 |
| 记忆 | PowerContext Server 1.4.0（SQLite） | Atomic Memory 显式读写无需模型；Dashboard 可核对 |
| 嵌入 | fastembed + `BAAI/bge-small-zh-v1.5`（512 维，MIT，90MB） | ONNX 轻量、中文、离线可用 |
| 中文分词 | jieba 预分词写入 `search_text` | 规避 seekdb 中文分词不确定性 |
| 前端 | React 18 + TypeScript + Vite + Ant Design 5 + ECharts | 组件全、中文生态、图表现成 |
| PDF | `pdfjs-dist` | 原版渲染 + 文本层定位 |
| 部署 | Docker Compose（`app` + `powercontext`） | 一键起；Windows 走 Docker |
| 测试 | pytest + httpx + Playwright（冒烟） | API 与关键 UI |
| 质量 | ruff + mypy + pre-commit | 工程质量维度 |

### 4.2 关键决策

- **D1** PowerContext 用独立 Server，不用进程内 builtin：评审可打开 Dashboard 核对记忆；与官方推荐用法一致。
- **D2** seekdb 由应用直接使用，不当作 PowerContext 后端：内容平面与状态平面分离，检索 SQL 可见。
- **D3** 回答默认抽取式，LLM 仅可选：评审无需 API key 即可跑通，避免「套壳 LLM」质疑。
- **D4** 中文全文用 jieba 预分词 + FULLTEXT：不赌 seekdb 的 ngram 支持；原文列完整保留。
- **D5** 只提供 Linux 容器路径：嵌入式 seekdb 不支持 Windows，统一走 Docker。

### 4.3 数据流

```
PDF → pypdf 页文本 → 切块 → jieba 分词 ─┬→ seekdb 写块（原文 + search_text + 向量）
                                        └→ 索引就绪
问题 → 混合检索(SQL) → 融合排序 → 证据选择 → 抽取式回答 + 引用
                                   ↓
                          PowerContext 写入：问题、命中、薄弱点
首页 ← 记忆聚合（画像/进度/薄弱点/到期卡片）← PowerContext 读取
```

## 5. 数据模型与 API

### 5.1 seekdb 表

| 表 | 关键字段 |
| --- | --- |
| `cl_documents` | id, title, authors, year, venue, sha256, path, pages, status, tags, created_at |
| `cl_chunks` | id, doc_id, page, section, text, search_text, char_start, char_end, embedding VECTOR(512) + HNSW + FULLTEXT |
| `cl_queries` | id, profile, question, hits_json, answer_json, latency_ms, created_at |
| `cl_cards` | id, profile, concept, source_ref, due_at, interval, ease, last_grade |
| `cl_events` | id, profile, type, payload, created_at |

### 5.2 HTTP API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/documents` | 多文件上传 |
| GET/DELETE | `/api/documents`、`/api/documents/{id}` | 列表/详情/删除 |
| POST | `/api/documents/{id}/reindex` | 重建索引 |
| GET | `/api/documents/{id}/pages/{page}` | 页文本 |
| POST | `/api/search` | 混合检索（分数、通道、SQL id） |
| POST | `/api/ask` | 问答（回答 + 引用 + 调试信息） |
| POST | `/api/compare` | 对比表 |
| GET/DELETE | `/api/answers/{id}` | 历史 |
| GET | `/api/memory/{profile}` | 记忆聚合视图 |
| POST | `/api/memory/{profile}/concept`、`/progress` | 写入记忆 |
| GET/POST | `/api/review`、`/api/review/{id}/grade` | 复习 |
| GET | `/api/analytics/summary` | 统计 |
| GET | `/api/debug/retrieval/{id}` | SQL / EXPLAIN / 分数 |

### 5.3 PowerContext 记忆 schema（Atomic Memory + tag）

| tag | 内容示例 |
| --- | --- |
| `cl:topic` | `{"topic":"efficient attention","weight":0.8}` |
| `cl:concept` | `{"name":"FlashAttention","status":"unclear","sources":["doc:xxx p.4"]}` |
| `cl:progress` | `{"doc":"arxiv:2305.xxxx","page":12,"status":"reading"}` |
| `cl:question` | `{"q":"为什么…","answer_id":"..."}` |
| `cl:preference` | `{"language":"zh","detail":"concise"}` |

## 6. 目录结构

```
qoder/citelens/
├── README.md
├── docs/
│   ├── PLAN.md                    # 本文件
│   ├── 需求与设计.md
│   ├── 运行说明.md
│   ├── 验证样例.md
│   ├── 技术证据.md
│   ├── Qoder开发记录.md
│   ├── 使用效果.md
│   ├── briefs/M1..M8.md           # 给 Qoder 的任务规格
│   └── qoder/                     # 截图与 CLI transcript
├── backend/
│   ├── pyproject.toml
│   ├── src/citelens/{api,core,ingest,retrieval,answer,memory,review,analytics}/
│   └── tests/
├── frontend/
│   ├── package.json、vite.config.ts
│   └── src/{pages,components,api,hooks}/
├── deploy/{Dockerfile,docker-compose.yml,.env.example,Makefile}
├── samples/{manifest.json,queries.json,expected/}
└── scripts/{fetch_samples.py,seed_demo.py,bench.py,export_evidence.py}
```

## 7. 实施计划

> 每阶段独立验收、≤30 分钟。总计约 24 小时纯执行，按每天 4 小时约 6 个工作日；另加 1–2 周真实使用。

### M0 立项与骨架（4 阶段）

| # | 内容 | 验收 |
| --- | --- | --- |
| S0.1 | 清理残留 `src/`、缓存；建骨架；更新 `.gitignore` | `git status` 只剩预期文件 |
| S0.2 | 落盘 PRD、架构、API、数据模型 | `docs/需求与设计.md` 可评审 |
| S0.3 | Docker 基座、healthcheck、空 FastAPI | `docker compose up` 后 `/health` 返回 ok |
| S0.4 | 前端脚手架、布局、路由、空页面 | 页面可打开，构建产物由后端托管 |

### M1 文献库（6 阶段）

| # | 内容 | 验收 |
| --- | --- | --- |
| S1.1 | 上传 + pypdf 页文本 + 元数据启发式 | 5 篇 PDF 解析成功，页码正确 |
| S1.2 | 切块 + jieba 分词 + 重叠策略 | 块数/长度合理，抽查通过 |
| S1.3 | seekdb 建表 + HNSW + FULLTEXT | 建表/写入/删除通过；EXPLAIN 见两类索引 |
| S1.4 | 文献库 UI（上传/列表/状态/删除） | 浏览器完成全流程 |
| S1.5 | 异常处理：加密/扫描版/超大/损坏 | 每类有可读错误，不崩溃 |
| S1.6 | 去重 + 重新索引 + 索引进度 | 重复上传提示；重建后结果一致 |

### M2 检索（6 阶段）

| # | 内容 | 验收 |
| --- | --- | --- |
| S2.1 | 混合检索单 SQL + 融合排序 | 两路候选合并，分数可解释 |
| S2.2 | 检索 API + 调试面板 | 显示 SQL、EXPLAIN、各路分数 |
| S2.3 | 固定 query 集评估 Recall@10 | 报告输出，Recall@10 ≥ 0.8 |
| S2.4 | 性能基准 p50/p95 | 1000 块 p50 < 300ms |
| S2.5 | 中文全文验证与调优 | 中文 query 全文通道有命中 |
| S2.6 | 检索日志 + 证据导出 | `logs/retrieval.jsonl` 可复核 |

### M3 问答与对比（8 阶段）

| # | 内容 | 验收 |
| --- | --- | --- |
| S3.1 | 证据选择（IDF + 邻句合并） | 三个问题的证据片段覆盖答案 |
| S3.2 | 抽取式回答 + 引用生成 | 每句带 `[论文 p.x]` |
| S3.3 | 回答 UI（引用卡片、跳转、复制） | 点击引用跳转正确 |
| S3.4 | 对比表生成 | 5 篇 × 5 列，每格有出处 |
| S3.5 | 对比表 UI + 导出 | Markdown/CSV 可打开 |
| S3.6 | 可选 LLM 增强（OpenAI 兼容） | 无 key 默认路径不受影响 |
| S3.7 | 回答历史 | 可回看、可删除 |
| S3.8 | 边界：无命中/超长/跨文档冲突 | 有明确提示与降级 |

### M4 记忆（8 阶段）

| # | 内容 | 验收 |
| --- | --- | --- |
| S4.1 | PowerContext 接入（compose、client、健康检查） | `/ready` 通过，Dashboard 可打开 |
| S4.2 | 记忆 schema 与写入（5 类 tag） | Dashboard 看到记录 |
| S4.3 | 记忆读取与聚合视图 | API 返回完整画像 |
| S4.4 | 首页续读体验 | 重启后显示上次进度与未懂概念 |
| S4.5 | 记忆面板 UI（CRUD） | 手动编辑后记忆同步 |
| S4.6 | 记忆日志 + 证据导出 | `logs/memory.jsonl` + 截图 |
| S4.7 | `prepare_context` 注入 | 回答时带上记忆上下文 |
| S4.8 | 多 profile 隔离 | 切换 profile 记忆互不干扰 |

### M5 阅读器与复习（6 阶段）

| # | 内容 | 验收 |
| --- | --- | --- |
| S5.1 | pdf.js 渲染 + 页跳转 | 打开 PDF 正常，跳页正确 |
| S5.2 | 引用高亮 + 文本选择 | 高亮位置准确 |
| S5.3 | 标记未懂写入记忆 | 记忆面板即时出现 |
| S5.4 | 阅读进度自动记录 | 关闭重开进度正确 |
| S5.5 | SM-2 调度 | 评分后到期时间变化 |
| S5.6 | 复习 UI | 卡片流程完整 |

### M6 统计与使用效果（4 阶段）

| # | 内容 | 验收 |
| --- | --- | --- |
| S6.1 | 事件埋点 | 每个操作产生一条事件 |
| S6.2 | 统计仪表盘 | 数字与操作一致 |
| S6.3 | 提效计时器 + 报告 | 可导出整理耗时对比 |
| S6.4 | 数据导出 JSON/CSV | 文件可用于提交材料 |

### M7 质量与文档（7 阶段）

| # | 内容 | 验收 |
| --- | --- | --- |
| S7.1 | 单测补齐（覆盖率 ≥70%） | `pytest --cov` 达标 |
| S7.2 | 集成/E2E 冒烟 | 一条命令跑通主流程 |
| S7.3 | 性能与稳定性（并发、大文件） | 无崩溃，延迟可控 |
| S7.4 | README / 运行说明 / 架构文档 | 新人按文档能起 |
| S7.5 | 验证样例（固定数据 + 预期结果） | 逐步可执行 |
| S7.6 | 技术证据文档 | 截图 + 日志 + SQL 齐全 |
| S7.7 | Qoder 开发记录整理 | 每里程碑 4–6 张截图 + transcript |

### M8 网站与提交（4 阶段）

| # | 内容 | 验收 |
| --- | --- | --- |
| S8.1 | 网站内容 + 截图/录屏 | 文案、图、视频就绪 |
| S8.2 | Qoder Sites 建站 | 预览通过 |
| S8.3 | 发布公开 + 无痕验证 | 未登录可访问 |
| S8.4 | 材料核对 + 仓库提交 + 平台填报 | 6 项材料齐全 |

## 8. 验证样例

| 步骤 | 操作 | 预期 |
| --- | --- | --- |
| 1 | `docker compose up` | 两个服务健康 |
| 2 | `scripts/fetch_samples.py`（5 篇 arXiv） | 下载 5 个 PDF |
| 3 | 文献库批量上传 | 5 篇均「已索引」 |
| 4 | 问「这几篇在处理长上下文时的主要瓶颈是什么？」 | ≥3 条引用，页码正确 |
| 5 | 问「对比它们的显存开销」→ 生成对比表 | 5 行 × 5 列，每格有出处 |
| 6 | 选中「KV cache 压缩」标记未懂 | 记忆面板出现该概念 |
| 7 | 关闭浏览器，重新打开 | 首页显示进度 + 未懂概念 + 复习卡 |
| 8 | 导出统计 CSV | 包含本次操作记录 |

技术证据：`docs/技术证据.md` 收录检索 SQL、EXPLAIN、`logs/retrieval.jsonl` 片段、PowerContext Dashboard 截图、`logs/memory.jsonl` 片段。

## 9. Qoder 协作模式

### 9.1 分工

| 角色 | 职责 |
| --- | --- |
| Qoder 桌面版（人工） | 关键里程碑：需求拆解、架构决策、Sites 建站/发布、演示录屏、截图 |
| Qoder CLI（DSH 编排） | 按 brief 执行阶段任务、修复、跑测试；transcript 落盘 |
| DSH | 写 brief/测试/脚本/文档、只读审查、验收、缺陷定位、提交整理 |

### 9.2 CLI 自动化约束

- 每条任务使用 `-p`（非交互）+ 明确的验收命令 + `--permission-mode bypass_permissions`。
- 输出用 `-o json` 落盘到 `docs/qoder/transcripts/`，作为开发记录的一部分。
- 失败重试上限 2 次，续会话用 `-r <session-id>`，避免 credits 失控。
- 每个里程碑结束由人工在桌面版复核并截图，防止「只有日志没有对话记录」。
- Sites 发布可用 `qoderclicn sites`，但公开前的预览与最终截图在桌面版完成。

## 10. 提交材料清单

| 材料 | 位置/说明 |
| --- | --- |
| 项目网站 URL | Qoder Sites 公开地址 |
| 代码仓库 | `52611314/powercontext-oceanbase`，分支 `2026-competition-ecosystem` |
| 运行说明 | `docs/运行说明.md` |
| 验证样例 | `docs/验证样例.md` + `samples/` |
| Qoder 开发记录 | `docs/Qoder开发记录.md` + `docs/qoder/` |
| 使用效果 | `docs/使用效果.md` + 导出数据 |
| Qoder UID | 桌面版「设置 → 个人资料 → 编辑」 |

## 11. 风险与对策

| 风险 | 影响 | 对策 |
| --- | --- | --- |
| seekdb 中文 FULLTEXT 不支持分词 | 全文通道失效 | jieba 预分词；失败则应用层 BM25 回退，向量仍走 seekdb |
| `pylibseekdb` 版本/语法差异 | 建表失败 | 先在容器实测；锁定版本写进 requirements |
| fastembed 模型下载失败 | 无法索引 | 提供离线模型目录 + `EMBEDDING_PROVIDER=hash` 回退 |
| pdf.js 高亮定位不准 | 体验扣分 | 先做页级跳转 + 文本搜索定位，必要时降级页文本视图 |
| PowerContext Windows 不可用 | 本机开发受阻 | 全部开发在 Docker 内 |
| Qoder credits 消耗 | 中途额度不足 | 一里程碑一任务，brief 写细，重试上限 2 次 |
| 工作量 24h+ | 延期 | 按里程碑交付，每个里程碑都可演示 |

## 12. 当前状态与下一步

| 项 | 状态 |
| --- | --- |
| Qoder CN 桌面版 | 已安装（`D:\Qoder`），已登录，有 Credits 与站点入口 |
| Qoder CN CLI | 已安装 1.1.79，路径 `C:\Users\13922\.qoder-cn\bin\qoderclicn\qoderclicn.exe`，**尚未登录** |
| 项目目录 | `qoder/citelens/` 已建，本文件已落盘 |
| 工作区残留 | `src/`（255 文件）与缓存待清理（S0.1） |

**下一步**：完成 S0.1（清理与骨架）→ S0.2（需求与设计）→ 写 M1 brief → 在 Qoder 中执行。
