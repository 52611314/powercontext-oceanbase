# seekdb × 阶跃星辰：端侧 AI 推理 + 混合检索迷你知识库

赛题原文见 `../task.md`。本文件只记录方案冻结内容与阶段进度，不替代技术说明。

## 1. 目标与硬约束

构建浏览器本地完成 embedding、由 seekdb 完成向量+全文混合检索的迷你知识库应用；提交可运行应用、完整源码、一页技术说明，总分 20。

硬约束（任一不满足即不合格）：

1. 文档与查询向量均在用户浏览器计算，禁止云端推理 API。
2. 向量、原文、元数据存 seekdb；存储与检索不得被其他数据库或客户端内存替代。
3. 混合检索在 seekdb 内完成（`hybridSearch` → `DBMS_HYBRID_SEARCH`，RRF 由库内计算），禁止客户端分别取两路结果拼合。
4. WebGPU 优先，不支持时自动降级 WASM 并明确提示。
5. 提交分支/commit 基于 `2026-competition-ecosystem`，产物放入 `stepfun/`。

## 2. 架构

```
Chrome/Edge
 ├─ Web Worker: Transformers.js (WebGPU → WASM 降级)
 │    ├─ 文档分块 → 批量向量化 → 进度
 │    └─ 查询向量化
 └─ React UI ──HTTP(localhost)──▶ Node BFF (Express + seekdb-js)
                                   └─▶ seekdb server :2881
                                        collection + HNSW + IK FULLTEXT
                                        hybridSearch(RRF)
```

BFF 只做协议转换与参数校验，不参与排序；检索排序完全由 seekdb 完成。

## 3. 技术选型（冻结）

| 项 | 选择 |
| --- | --- |
| 主模型 | `bge-small-zh-v1.5`（512 维），技术说明注明来源与版本 |
| 量化 | FP32 基线 / FP16（WebGPU）/ INT8 dynamic（WASM） |
| 前端 | Vite + React + TypeScript |
| 推理 | `@huggingface/transformers` v3，Web Worker 内执行 |
| 后端 | Node 20+ / Express / seekdb-js |
| 数据库 | Docker `oceanbase/seekdb`，端口 2881，数据卷持久化 |
| 评测集 | 自建 200–300 chunk 中英语料 + 30 条查询 + 人工 qrels |
| 指标 | Recall@10、nDCG@10、MRR@10 |
| 运行形态 | `npm run build && npm start`，后端托管静态文件（单端口） |

## 4. 阶段计划与验收

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| 0 | Docker 起 seekdb；seekdb-js 建集合、写向量、刷新、混合检索；`GET_SQL` 验证全文分支 | 脚本输出实际 SQL 含 MATCH/FULLTEXT；混合结果非空；npm 缓存落 D 盘 |
| 1 | 下载 bge-small-zh-v1.5 ONNX；量化出 FP32/FP16/INT8 | 三份文件 + 体积表 + 压缩比 |
| 2 | BFF 接口：collections / documents / search / refresh / delete | curl 全接口通过 |
| 3 | 推理 Worker：WebGPU 检测、WASM 降级、批处理、进度 | 后端徽标正确；向量与 Python 参考余弦 > 0.999 |
| 4 | 前端 UI：导入、库切换、检索、结果高亮、计时 | 导入→刷新→检索可见 |
| 5 | 端到端联调：增量导入、库切换、删除一致性 | 完整流程通过 |
| 6 | 基准测试：多配置 × 30 查询 × 3 次 | results.json + 中位数表 |
| 7 | 文档：README + 一页技术说明 + 复现步骤 | 按 README 可复现 |
| 8 | 提交推送并核对远程 SHA | `git ls-remote` 一致 |

## 5. 进度

- 2026-10-27 10:02 阶段 0 开始，10:12 通过。
  - 复用本机已编译的 seekdb 1.4.0.0：复制到 `.build-env/seekdb-kb-runtime/`，容器 `seekdb-kb` 只读挂载该副本，数据目录放容器内盘，端口 127.0.0.1:2881。
  - 验证：建库建集合（512 维 cosine + IK 全文，禁用库内 embedding）、写 5 条、`refresh_index` 前近似检索 0 命中 / 刷新 484ms 后 3 命中、`hybridSearch` 33ms。
  - 关键证据：`DBMS_HYBRID_SEARCH.GET_SQL` 生成的 SQL 含 `match(document) against('…' in natural language mode)`、`cosine_distance` 与 RRF `1/(rank+60)` 的 full join —— 混合检索在库内完成。
  - 记录：`scripts/phase0-result.json`。
- 2026-10-27 10:12 阶段 1 开始，10:48 通过。
  - 下载 FP32 ONNX 90.46 MB、官方 FP16 45.30 MB；自量化 INT8 22.81 MB。
  - 压缩比：FP16 2.00x，INT8 3.97x；INT8 与 FP32 句向量余弦 0.956 / 0.959。
  - 记录：`scripts/model-quantize-result.json`、`public/models/bge-small-zh-v1.5/onnx/`。
  - 网络事实：HF 直连约 0.03 MB/s、hf-mirror 约 0.025 MB/s、ModelScope 约 0.06 MB/s；本机 SOCKS5 1080 约 1.15 MB/s。下载脚本用 `MODEL_SOCKS=socks5h://127.0.0.1:1080`。
- 2026-10-27 11:56 阶段 2 开始，12:13 通过。
  - `server/db.mjs` + `server/index.mjs`：health、collections CRUD、documents 批量写入（自动 refresh）、delete-documents、hybridSearch、refresh，含集合名/维度/条数/元数据校验。
  - 冒烟 `scripts/phase2-smoke.mjs` 全绿：写入 3 条 + refresh 452ms、混合检索 71ms 且 doc-1 第一、增量写入刷新后可检索、删除后 count 正确、4 个错误分支返回 400/404。
  - 两个坑：`getCollection` 必须显式 `embeddingFunction: null`，否则 SDK 尝试加载未安装的 `default-embed`；Docker Desktop 停止后需重启并 `docker start seekdb-kb`。
- 2026-10-27 12:14 阶段 3 开始，13:30 通过（两轮修复后）。
  - 已实现：`src/worker/embedding.worker.ts`（WebGPU 探测 → fp16，失败/不可用降级 WASM q8；本地 `/models` 加载；query 加 bge 检索指令；批量 + 进度回调）、`src/lib/embedding-client.ts`（Promise 化 Worker）、`src/App.tsx`（后端徽标、加载进度、向量自检、FP32 vs INT8 对比）、Vite/TS 配置。
  - 浏览器验收（Edge 156.0.4314.42，headless + CDP，`scripts/browser-check.mjs`）：
    - WebGPU 路径：徽标 `WebGPU · FP16`，512 维，163–178ms，示例余弦 0.5549；证据 `scripts/browser-check-webgpu.json`。
    - WASM 降级：`--disable-gpu` 下 `navigator.gpu` 探测失败（adapter=null）→ 徽标 `WASM · Q8`，512 维，53ms；证据 `scripts/browser-check-wasm.json`。
    - 量化一致性（WASM，同一文本 FP32 vs INT8）：余弦 0.966584，FP32 44ms / INT8 40ms。
  - 两个坑：Vite 的 worker 插件解析 `new Worker(new URL(...))` 时调用 `exec`，普通沙箱报 `spawn EPERM`，导致 `/src/lib/embedding-client.ts` 500、页面空白；Vite 必须以 danger-full-access 运行。CDP 的 `Runtime.evaluate` 不能用 `returnByValue` 取 DOM 元素，条件必须写成布尔表达式。
  - 风险：INT8 量化后句向量余弦 0.9666（Python 端 0.9559/0.9587），低于理想值；阶段 6 用 Recall@10 实测影响，必要时改用官方 INT8（24.01MB）或跳过部分层量化。
- 2026-10-27 13:00 阶段 4 开始，13:35 通过；阶段 5 同批完成。
  - 前端组件：`src/components/Sidebar.tsx`（库列表/新建/删除）、`ImportPanel.tsx`（文件或粘贴、400 字分块/50 重叠、批量向量化进度、写库计时）、`SearchPanel.tsx`（查询、RRF 分数、关键词高亮、单条删除、分阶段计时）、`src/lib/api.ts`、`src/lib/chunk.ts`；`src/App.tsx` 组装两栏布局并自动创建 `default` 库。
  - TypeScript `tsc --noEmit` 通过（修复 Worker 隐式 any 与 device/dtype 类型）。
  - 端到端（Edge headless，`scripts/e2e-check.mjs`）：WebGPU·FP16；导入 DOC-A 1 块（向量化 130ms / 写入 74ms / 刷新 636ms / 合计 850ms）；检索返回 DOC-A；新建 e2e_b 导入 DOC-B 后检索只返回 DOC-B（隔离 true）；切回 e2e_a 只返回 DOC-A（隔离 true）；删除当前库成功；截图 `scripts/e2e-screenshot.png`，结果 `scripts/e2e-result.json`。
  - 检索计时示例：查询向量化 29ms、seekdb 混合检索 7ms、候选 40、端到端 46ms。
- 2026-10-27 13:05 阶段 6 开始，13:40 通过。
  - 评测集：`eval/corpus.json`（20 篇中文技术短文，每篇 1 块）、`eval/queries.json`（30 条查询 + qrels）。
  - 基准脚本 `scripts/benchmark.mjs`（CDP 驱动 Edge：导入语料 → 30 查询 → Recall@10/nDCG@10/MRR@10），`scripts/summarize-benchmark.mjs` 汇总。
  - 结果（Edge 156，同机同语料）：FP32 基线 Recall 1.0000 / nDCG 0.9186；FP16（WebGPU）完全一致；INT8（WASM）Recall 0.9667（-3.3pt）、nDCG 0.8860。
  - 平均耗时：导入向量化 FP32 86.7ms / FP16 45.1ms / INT8 57.6ms；查询向量化 25.5 / 30.8 / 17.3ms；seekdb 检索 8.7 / 9.0 / 10.0ms；端到端 44.6 / 50.3 / 38.0ms。
  - 记录：`eval/benchmark-summary.md`、`scripts/benchmark-*.json`。
- 2026-10-27 13:45 阶段 7 开始，14:10 通过。
  - 生产模式：`server/index.mjs` 增加 `dist/` 静态托管与 SPA 回退；`npm run build && npm start` 单端口 8787 验证页面/API/模型文件均 200。
  - 文档：`README.md`（启动、模型、基准、目录、验证记录）、`docs/技术说明.md`（模型与量化、端侧运行、seekdb 使用、状态与优化）、`docs/benchmark.md`（条件、复现、局限）、`docker-compose.yml`、`.env.example`、`.gitignore`。
  - 清理：删除临时诊断脚本 `scripts/diag-edge.mjs`。
- 待办：阶段 8 提交推送。
