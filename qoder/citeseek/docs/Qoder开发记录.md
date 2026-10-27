# Qoder 开发记录

> 本文件记录 Qoder 在 CiteSeek 开发中的实际工作。Qoder 对话截图、录屏与账号 UID 由参赛者补充。
> 项目：CiteSeek（文献与资料检索工作台）
> 分支：`2026-competition-ecosystem`，目录：`qoder/citeseek/`

## 1. 账号与素材

| 项 | 内容 |
| --- | --- |
| Qoder 账号 UID | `01a17892-0f36-79b6-83d6-40e5d75abc8c` |
| 项目网站 | <https://citeseek-ma9lrveqlz1.qoder.zone>（Qoder Sites，已公开） |
| 产品截图 | `site/assets/` 下的 `library`、`search`、`evidence`、`reader`、`ask` 五张 WebP，均为运行中应用的实拍 |
| Qoder 对话截图 | 建议放在 `qoder/开发记录/`，命名 `01-需求拆解.png` … `08-站点发布.png` |
| 录屏 | 建议 2–3 分钟，覆盖上传、检索、问答、引用跳转、证据抽屉 |

## 2. 开发时间线

| 阶段 | 日期 | 由 Qoder 完成的工作 | 产物与验证 |
| --- | --- | --- | --- |
| P0 技术探针 | 2026-10-27 | 拉起 seekdb 容器；验证 `VECTOR(512)` + `WITH PARSER ik` 建表；验证单条 SQL 双通道与 EXPLAIN；验证 ONNX 嵌入 | `scripts/probes/probe_e2e.py`；EXPLAIN 同时出现 `TEXT RETRIEVAL SCAN` 与 `VECTOR INDEX SCAN` |
| P1 资料库 | 2026-10-27 | 搭 FastAPI 骨架；实现 PDF/MD/TXT 解析、切块、嵌入、入库、去重、进度、删除重建；Docker Compose；单元测试 | 3 份 PDF 导入成功，14 项测试通过 |
| P2 混合检索 | 2026-10-27 | 实现 CTE 双通道 SQL、条件下推、RRF 融合、高亮、调试接口、检索日志 | `verify_search.py` 通过；hybrid 7.7 ms；证据落盘 |
| P3 出处与问答 | 2026-10-28 | 实现抽取式证据选择、逐句引用（页码 + 字符区间）、原文访问、Markdown/BibTeX 导出、回答历史 | `verify_ask.py` 通过；引用偏移与原文逐条比对一致 |
| P4 前端 | 2026-10-28 | 搭 React + TS + Vite + Ant Design；实现文献库、检索、问答、阅读四个页面；证据抽屉与原文高亮 | 浏览器实测四个页面；容器内 `/` 返回 SPA；控制台无报错 |
| P5 评测与站点 | 2026-10-28 | 建立 21 条查询的评测集；实现 Hit@10/MRR/章节召回；从运行中的应用抓取 5 张真实界面截图；搭建 Qoder Sites 项目页并公开发布；补齐运行说明、验证样例、提交材料 | `docs/检索评测.md`（三模式 Hit@10 100% / 95.2% / 100%）；（Qoder Sites 地址见上表） |

## 3. Qoder 参与的具体环节

| 环节 | 具体工作 |
| --- | --- |
| 需求拆解 | 阅读赛题，拆出硬性验收与评分维度；确定「文献检索」方向与技术边界 |
| 技术选型 | 对比 seekdb 嵌入式与服务端模式，选择服务端 + MySQL 协议（可核对）；选择 IK 分词、int8 ONNX 模型随仓库分发 |
| 编码 | 后端 20 个模块文件、前端 12 个组件/页面、部署与脚本；全部代码由 Qoder 生成并修改 |
| 调试 | 修复 `pymysql.ping(reopen=...)` 参数错误、容器路径不可移植、PDF 软换行切句、跨章节切块、表格布局溢出等问题 |
| 验证 | 编写并执行 4 个验收脚本、32 项单元测试、ruff 检查；用内置浏览器逐页验收 |
| 文档 | 生成运行说明、验证样例、技术证据、评测报告与 README |

## 4. 可核对记录

- 每次验收脚本的输出（`OK`）与 `docs/evidence/*.json`
- `data/logs/retrieval.jsonl` 中的逐次检索记录
- Git 提交：`aa78ece0`（移除旧脚手架）、`17ddfb60`（CiteSeek 实现与站点），已推送 `origin/2026-competition-ecosystem`
- 建议截图清单：
  1. 需求拆解与方案讨论（Qoder 对话，待补）
  2. P0 探针 EXPLAIN 输出（`docs/evidence/search-evidence.json` 可复现）
  3. 资料库导入完成（`site/assets/library.webp`）
  4. 混合检索结果与证据抽屉（`site/assets/search.webp`、`evidence.webp`）
  5. 问答引用跳转（`site/assets/ask.webp`、`reader.webp`）
  6. 前端构建与 Docker 启动（终端记录，待补）
  7. 评测报告（`docs/检索评测.md`）
  8. Qoder Sites 发布成功页（<https://citeseek-ma9lrveqlz1.qoder.zone>）

## 5. 说明

- 项目主要开发工作在 Qoder 桌面版中完成；本记录中的阶段与实际代码、脚本、测试一一对应。
- 未使用其他 AI 工具生成业务代码；第三方开源库与模型来源见 README 与 `docs/技术证据.md`。
