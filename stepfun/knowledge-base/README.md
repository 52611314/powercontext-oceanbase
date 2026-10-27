# seekdb 迷你知识库

> 2026 OceanBase 大赛 · seekdb × 阶跃星辰赛题：端侧 AI 推理性能优化与 seekdb 混合检索

浏览器本地完成 embedding 推理，seekdb 完成向量 + 全文混合检索的迷你知识库应用。

## 特性

- **端侧推理**：Transformers.js + ONNX Runtime Web，WebGPU 优先，不可用时自动降级 WASM 并在界面提示；
- **模型量化**：bge-small-zh-v1.5，INT8 22.81 MB / FP16 45.30 MB / FP32 90.46 MB，压缩比 3.97x / 2.00x；
- **库内混合检索**：一次 `DBMS_HYBRID_SEARCH`（`MATCH ... AGAINST` + `cosine_distance` + RRF），客户端不拼合两路结果；
- **异步索引刷新**：写入后调用 `refresh_index()`，新文档刷新后立即可检索；
- **多知识库**：一个知识库对应一个 seekdb collection，切换后检索只作用于当前库；
- **可复现基准**：20 篇语料 / 30 条查询，三档量化配置的 Recall@10、nDCG@10、MRR@10 与分阶段耗时。

## 架构

```
Edge / Chrome
 ├─ Web Worker: Transformers.js（WebGPU fp16 → WASM q8 自动降级）
 │    ├─ 文档分块 → 批量向量化（进度回调）
 │    └─ 查询向量化
 └─ React UI ──HTTP──▶ Node BFF（Express + seekdb-js，127.0.0.1:8787）
                        └─▶ seekdb server :2881
                             collection(kb_*) + HNSW + IK FULLTEXT
                             hybridSearch(query ∪ knn, RRF)
```

BFF 只做协议转换与参数校验，不参与排序；检索排序完全由 seekdb 的 `DBMS_HYBRID_SEARCH` 完成。

## 快速开始

### 环境要求

- Node.js ≥ 20（本机验证 24.13.0）
- Docker Desktop / Docker Engine（用于 seekdb server）
- Chrome 或 Edge 最新版（WebGPU 需要；WASM 后端无 GPU 要求）

### 1. 启动 seekdb

```bash
docker compose up -d
# 或手动：
# docker run -d --name seekdb-kb -p 127.0.0.1:2881:2881 \
#   -e MEMORY_LIMIT=2G -e DATAFILE_SIZE=512M -e LOG_DISK_SIZE=2G \
#   oceanbase/seekdb:latest
```

等待约 10 秒，`mysql -h127.0.0.1 -P2881 -uroot` 能连上即可。

> 本仓库开发时使用的是自行编译的 seekdb 1.4.0.0 二进制（Docker 内运行），与官方镜像协议一致。
> 无 Docker 的 Linux/macOS 环境可改用 seekdb 嵌入式，但 Windows 不支持嵌入式，需要 server 模式。

### 2. 安装依赖

```bash
npm install --legacy-peer-deps
```

> `seekdb` 包的 peer 依赖 `@seekdb/default-embed` 发布包里残留 `workspace:*` 协议，npm 11 需要 `--legacy-peer-deps`。

### 3. 开发模式

```bash
npm run dev        # Vite 5173，/api 代理到 BFF 8787
npm run bff        # 另一个终端：BFF 8787
```

打开 <http://127.0.0.1:5173/>。

> Vite 8 在 Windows 上解析 Worker URL 时会调用 `exec` 获取 realpath；如果遇到 `spawn EPERM`（沙箱/受限终端），改用 `npm run build && npm start` 的生产模式。

### 4. 生产模式（推荐评测）

```bash
npm run build
npm start          # 单端口 8787，同时提供页面、API 与模型文件
```

打开 <http://127.0.0.1:8787/>。

## 模型

| 精度 | 文件 | 体积 | 压缩比 | 用途 |
| --- | --- | --- | --- | --- |
| FP32 | `public/models/bge-small-zh-v1.5/onnx/model.onnx` | 90.46 MB | 1.00x | 质量基线 |
| FP16 | `.../model_fp16.onnx` | 45.30 MB | 2.00x | WebGPU 默认 |
| INT8 | `.../model_quantized.onnx` | 22.81 MB | 3.97x | WASM 默认 |

- 来源：<https://huggingface.co/Xenova/bge-small-zh-v1.5>（Transformers.js 格式，源自 `BAAI/bge-small-zh-v1.5`），512 维；
- FP16 使用官方转换版本；INT8 由本仓库 `scripts/quantize-model.py` 用 `onnxruntime.quantization.quantize_dynamic` 生成；
- 重新下载：`MODEL_SOCKS=socks5h://127.0.0.1:0 node scripts/download-model.mjs`（如直连慢可填本机 SOCKS5 端口）；
- 重新量化：

  ```bash
  python -m venv .venv && .venv/Scripts/pip install onnx onnxruntime onnxconverter-common numpy
  .venv/Scripts/python scripts/quantize-model.py
  ```

## 复现基准

```bash
npm run bench:q8      # WASM + INT8
npm run bench:fp16    # WebGPU + FP16
npm run bench:fp32    # WASM + FP32
npm run bench:summary # 汇总成 eval/benchmark-summary.md
```

基准脚本会用独立的 Edge profile 启动 headless 浏览器（不碰日常浏览器），自动导入 `eval/corpus.json`、跑 `eval/queries.json`，输出 `scripts/benchmark-*.json`。

结果见 [docs/benchmark.md](docs/benchmark.md)，技术说明见 [docs/技术说明.md](docs/技术说明.md)。

## 目录结构

```
stepfun/knowledge-base/
├─ src/                    # 前端（React + TS）
│  ├─ worker/embedding.worker.ts   # WebGPU/WASM 推理 Worker
│  ├─ lib/{api,chunk,embedding-client}.ts
│  └─ components/{Sidebar,ImportPanel,SearchPanel}.tsx
├─ server/                 # BFF（Express + seekdb-js）
│  ├─ db.mjs               # 连接、集合命名、维度校验
│  └─ index.mjs            # 路由 + 静态托管
├─ public/models/          # 本地模型（三档精度）
├─ eval/                   # 语料、查询、qrels、基准汇总
├─ scripts/                # 下载、量化、阶段验证、基准、e2e
├─ docs/                   # 技术说明、基准详情
├─ docker-compose.yml
└─ PLAN.md                 # 方案与阶段进度
```

## 验证记录

| 验证 | 入口 | 结果 |
| --- | --- | --- |
| seekdb 写入/刷新/混合检索 | `npm run phase0` | 刷新前 0 命中 → 刷新后 3 命中，SQL 含 `MATCH AGAINST` + RRF |
| BFF 全接口 | `npm run test:bff` | 全绿，含 4 个错误分支 |
| 浏览器推理 | `node scripts/browser-check.mjs --headless` | WebGPU·FP16 / WASM·Q8 双路径，512 维 |
| 端到端 | `node scripts/e2e-check.mjs --headless` | 导入、检索、跨库隔离、删除全部通过 |
| 基准 | `npm run bench:*` | 见 docs/benchmark.md |

## 注意事项

- 推理全程在浏览器本地，模型文件由本服务同源提供，不调用任何云端推理 API；
- 混合检索只调用一次 `collection.hybridSearch()`，全文与向量候选由 seekdb 内部 full join + RRF 完成；
- `metadata` 仅支持字符串/数字/布尔值，集合名需为 3–40 位小写字母、数字、`_` 或 `-`。
